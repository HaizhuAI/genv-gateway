"""Model registry: OpenAI-facing id -> upstream endpoint/fields.

turnstile: True  -> upstream requires a Cloudflare Turnstile token
                    (pass via request field `turnstile_token` or header X-Turnstile-Token)
credit:   0      -> free/0-credit model (vela family)
sizes map OpenAI-ish sizes -> upstream orientation/aspect values.
"""

# upstream video submit fields: prompt, model, duration, orientation, resolution, files[], mode
# channel: "web" -> api.snapgen.ai/api (x-guard-id), "mobile" -> api.geminigen.ai (x-app-version)
VIDEO_MODELS = {
    # ---- mobile channel (no turnstile, captured from the app) ----
    "muse-video":      {"channel": "mobile", "endpoint": "/mobile/v4/video-gen/muse",
                        "model": "muse-ai-video", "durations": [10],
                        "orientations": {"square", "portrait", "landscape"},
                        "resolutions": set(), "turnstile": False, "credit": 0, "quota": "muse_ai_video"},
    "grok-1.5-lite":   {"channel": "mobile", "endpoint": "/mobile/v4/video-gen/grok-1.5-lite",
                        "model": "grok-1.5-lite", "durations": [6, 10],
                        "orientations": {"square", "portrait", "landscape"},
                        "resolutions": {"480p", "720p"}, "turnstile": False, "credit": 0,
                        "orientation_field": "aspect_ratio", "quota": "grok_lite"},
    "veo-3.1-fast":    {"channel": "mobile", "endpoint": "/mobile/v4/video-gen",
                        "model": "veo-3.1-fast", "durations": [8],
                        "orientations": {"square", "portrait", "landscape"},
                        "resolutions": {"720p", "1080p"}, "turnstile": False,
                        "orientation_field": "aspect_ratio", "image_field": "image",
                        "ratio_literal": True,
                        "extra": {"service_mode": "stable"}, "quota": "veo"},
    "veo-3.1-lite":    {"channel": "mobile", "endpoint": "/mobile/v4/video-gen",
                        "model": "veo-3.1-lite", "durations": [8],
                        "orientations": {"square", "portrait", "landscape"},
                        "resolutions": {"720p", "1080p"}, "turnstile": False,
                        "orientation_field": "aspect_ratio", "image_field": "image",
                        "ratio_literal": True,
                        "extra": {"service_mode": "stable"}, "quota": "veo"},
    "veo-3.1":         {"channel": "mobile", "endpoint": "/mobile/v4/video-gen",
                        "model": "veo-3.1", "durations": [8],
                        "orientations": {"square", "portrait", "landscape"},
                        "resolutions": {"720p", "1080p"}, "turnstile": False,
                        "orientation_field": "aspect_ratio", "image_field": "image",
                        "ratio_literal": True,
                        "extra": {"service_mode": "stable"}, "quota": "veo"},
    # ---- web channel ----
    "vela-video":      {"endpoint": "/video-gen/vela",        "model": "vela-1",        "durations": [5, 10],  "orientations": {"square", "portrait", "landscape"}, "resolutions": {"480p", "720p"}, "turnstile": False, "credit": 0},
    "meta-video":      {"endpoint": "/video-gen/meta",        "model": "meta-1",        "durations": [10],     "orientations": {"square", "portrait", "landscape"}, "resolutions": {"480p", "720p"}, "turnstile": True},
    "vids-video":      {"endpoint": "/video-gen/vids",        "model": "vids-1",        "durations": [5, 10],  "orientations": {"square", "portrait", "landscape"}, "resolutions": {"480p", "720p"}, "turnstile": True},
    "sd-2-lower":      {"endpoint": "/video-gen/seedance",    "model": "sd-2-lower",    "durations": [5, 10],  "orientations": {"square", "portrait", "landscape"}, "resolutions": {"480p", "720p"}, "turnstile": False, "credit": 0, "mode": "lower"},
}

# upstream image submit fields: prompt, orientation|aspect_ratio, model?, resolution, files[], file_urls[], num_result
IMAGE_MODELS = {
    # ---- mobile channel (no turnstile) ----
    "nano-banana-pro":  {"channel": "mobile", "endpoint": "/mobile/v4/generate_image",
                         "model": "nano-banana-pro", "orientation_field": "aspect_ratio",
                         "ratio_literal": True,
                         "resolutions": {"1K", "2K", "4K"}, "turnstile": False, "quota": "nano"},
    "nano-banana-2":    {"channel": "mobile", "endpoint": "/mobile/v4/generate_image",
                         "model": "nano-banana-2", "orientation_field": "aspect_ratio",
                         "ratio_literal": True,
                         "resolutions": {"1K", "2K", "4K"}, "turnstile": False, "quota": "nano"},
    # ---- web channel ----
    "vela-image":       {"endpoint": "/vela/generate",        "orientation_field": "orientation",  "extra": {}, "turnstile": True, "credit": 0},
    "grok-image":       {"endpoint": "/imagen/grok",          "orientation_field": "orientation",  "extra": {"num_result": "1"}, "turnstile": True},
    "gpt-image-2":      {"endpoint": "/imagen/gpt-image-2",   "orientation_field": "orientation",  "extra": {}, "turnstile": True},
}

SIZE_TO_ORIENTATION = {
    "1024x1024": "square", "1:1": "square", "square": "square",
    "1024x1792": "portrait", "9:16": "portrait", "720x1280": "portrait", "portrait": "portrait",
    "1792x1024": "landscape", "16:9": "landscape", "1280x720": "landscape", "landscape": "landscape",
}


def orientation_for(size, allowed):
    o = SIZE_TO_ORIENTATION.get((size or "").lower(), "square")
    return o if not allowed or o in allowed else sorted(allowed)[0]


ORIENT_TO_RATIO = {"square": "1:1", "portrait": "9:16", "landscape": "16:9"}


def ratio_for(size):
    return ORIENT_TO_RATIO.get(orientation_for(size, None), "9:16")


def list_models():
    out = []
    for mid, m in VIDEO_MODELS.items():
        out.append({"id": mid, "object": "model", "owned_by": "snapgen",
                    "type": "video", "channel": m.get("channel", "web"),
                    "free": m.get("credit") == 0,
                    "turnstile": m.get("turnstile", False)})
    for mid, m in IMAGE_MODELS.items():
        out.append({"id": mid, "object": "model", "owned_by": "snapgen",
                    "type": "image", "channel": m.get("channel", "web"),
                    "free": m.get("credit") == 0,
                    "turnstile": m.get("turnstile", False)})
    return out
