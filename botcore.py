"""Shared QQ-bot command handling — transport-agnostic.

Commands (group: @bot first; private: direct):
  /help                  usage
  /model                 available models
  /<model_id> <prompt>   generate  e.g. /muse-video 一只猫在海边
  /pool                  pool stats
  /refill <n>            provision n accounts (admins only, empty list = all)
"""
import asyncio, time, uuid

import usage
import models as M

POLL_EVERY, POLL_MAX = 12, 40  # 12s x 40 = 8min

HELP = ("命令:\n"
        "/model            可用模型列表\n"
        "/<模型> <提示词>   生成视频/图片\n"
        "  例: /muse-video 一只猫在海边奔跑\n"
        "      /grok-1.5-lite 未来城市夜景\n"
        "      /nano-banana-2 一只红狐狸（图片）\n"
        "/pool             号池状态\n"
        "/refill <n>       补充账号")


def model_list_text():
    vs = [m for m, s in M.VIDEO_MODELS.items() if not s.get("turnstile")]
    im = [m for m, s in M.IMAGE_MODELS.items() if not s.get("turnstile")]
    return "视频模型: " + ", ".join(vs) + "\n图片模型: " + ", ".join(im)


def pool_text(provider):
    p = usage.policy()
    stats = usage.account_stats(provider.app_accounts)
    usable = sum(1 for a in stats if not a["ad_gated"])
    honor = sum(1 for a in stats if a.get("honor") and not a["ad_gated"])
    free = sum(a["free_left"] for a in stats)
    return (f"号池: {len(stats)} 账号 / {usable} 可用（荣誉 {honor}）/ 剩免费 ~{free} 次\n"
            f"自动补充: 阈值 {p['min_usable']}, 批次 {p['refill_batch']}, "
            f"{'开' if p['auto'] else '关'}")


def _spec_for(mid):
    if mid in M.VIDEO_MODELS:
        return "video", M.VIDEO_MODELS[mid]
    if mid in M.IMAGE_MODELS:
        return "image", M.IMAGE_MODELS[mid]
    return None, None


async def submit(provider, kind, mid, prompt):
    spec = _spec_for(mid)[1]
    if not spec:
        return None, f"未知模型 {mid}，用 /model 看列表"
    if spec.get("turnstile"):
        return None, f"{mid} 需要 turnstile，bot 暂不支持"
    fields = dict(spec.get("extra") or {})
    fields["prompt"] = prompt
    fields["model"] = spec["model"]
    if kind == "video":
        fields["duration"] = str(spec["durations"][0])
        fields[spec.get("orientation_field", "orientation")] = \
            "9:16" if spec.get("ratio_literal") else "portrait"
        fields["resolution"] = sorted(spec["resolutions"])[0] if spec.get("resolutions") else ""
    else:
        fields[spec.get("orientation_field", "orientation")] = \
            (M.ratio_for("1024x1024") if spec.get("ratio_literal") else "square")
        fields["resolution"] = sorted(spec["resolutions"])[0] if spec.get("resolutions") else None
    try:
        if spec.get("channel") == "mobile":
            d = await provider.mobile_submit(spec["endpoint"], fields)
        else:
            d = await provider.submit(spec["endpoint"], fields)
    except Exception as e:
        return None, f"提交失败: {e}"
    return d.get("uuid"), None


async def _poll_and_report(say, provider, kind, mid, uuid_):
    for _ in range(POLL_MAX):
        await asyncio.sleep(POLL_EVERY)
        try:
            h = await provider.mobile_history(uuid_)
        except Exception:
            continue
        st = h.get("status")
        if st == 2:
            items = h.get("generated_video" if kind == "video" else "generated_image") or []
            url = None
            if items:
                url = items[0].get("file_download_url") or items[0].get("video_url") \
                      or items[0].get("image_url")
            await say(f"[{mid}] 完成\n{url or '(无下载链接)'}")
            return
        if st == 3:
            await say(f"[{mid}] 失败: {h.get('error_code')} {h.get('error_message') or ''}")
            return
    await say(f"[{mid}] 超时仍在处理: {uuid_}")


async def run_command(text, provider, JOBS, say, is_admin=True, admins=None):
    """Handle one bot message. `say` = async callable(str).
    is_admin/admin: controls /refill only (admins empty -> everyone allowed)."""
    body = text.strip().lstrip("/").strip()
    if not body:
        return
    head, _, rest = body.partition(" ")
    cmd = head.strip()

    if cmd in ("help", "帮助", "menu"):
        await say(HELP)
        return
    if cmd in ("model", "models", "模型"):
        await say(model_list_text())
        return
    if cmd in ("pool", "号池", "status"):
        await say(pool_text(provider))
        return
    if cmd in ("refill", "补号"):
        if admins and str(is_admin) not in [str(a) for a in admins] and not is_admin:
            await say("无权限")
            return
        try:
            n = min(int(rest.split()[0]), 10)
        except Exception:
            n = 3
        await say(f"开始补充 {n} 个账号…")
        for _ in range(n):
            try:
                rec = await provider.provision_app_account()
                await say(f"+ {rec['email']}")
            except Exception as e:
                await say(f"建号失败: {str(e)[:120]}")
                break
        return

    kind, spec = _spec_for(cmd)
    if spec:
        prompt = rest.strip()
        if not prompt:
            await say(f"用法: /{cmd} <提示词>")
            return
        await say(f"[{cmd}] 已提交: {prompt[:60]}")
        u, err = await submit(provider, kind, cmd, prompt)
        if err:
            await say(err)
            return
        jid = ("video_" if kind == "video" else "img_") + uuid.uuid4().hex[:16]
        JOBS[jid] = {"uuid": u, "type": kind, "model": cmd,
                     "channel": spec.get("channel", "mobile"),
                     "created": time.time(), "attempts": 1, "history": {}}
        asyncio.create_task(_poll_and_report(say, provider, kind, cmd, u))
        return

    await say(f"未知命令 /{cmd}，发 /help 看用法")
