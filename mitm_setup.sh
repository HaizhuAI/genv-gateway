#!/bin/bash
# MITM 透明代理部署（在 VM 的 MSYS bash 里跑）
ADB=/c/Tools/android-sdk/platform-tools/adb.exe
DEV=emulator-5554
CAHASH=c8750f0d
set -x

$ADB -s $DEV root
sleep 3
$ADB -s $DEV wait-for-device
$ADB -s $DEV shell "mount -o rw,remount /system" || true
$ADB -s $DEV shell "mount -o rw,remount /" || true
# 新版安卓证书目录
$ADB -s $DEV shell "mount -o rw,remount /apex/com.android.conscrypt" 2>/dev/null || true
$ADB -s $DEV push /c/Users/devin/.mitmproxy/mitmproxy-ca-cert.pem /sdcard/$CAHASH.0
$ADB -s $DEV shell "cp /sdcard/$CAHASH.0 /system/etc/security/cacerts/$CAHASH.0 2>/dev/null || cp /sdcard/$CAHASH.0 /apex/com.android.conscrypt/cacerts/$CAHASH.0"
$ADB -s $DEV shell "chmod 644 /system/etc/security/cacerts/$CAHASH.0 /apex/com.android.conscrypt/cacerts/$CAHASH.0 2>/dev/null; ls -la /system/etc/security/cacerts/$CAHASH.0 /apex/com.android.conscrypt/cacerts/$CAHASH.0 2>/dev/null"
# hosts 映射 api.geminigen.ai -> 10.0.2.2 (宿主)
$ADB -s $DEV shell "mount -o rw,remount /system; grep -q geminigen /system/etc/hosts || echo '10.0.2.2 api.geminigen.ai' >> /system/etc/hosts; cat /system/etc/hosts"
# 443 -> 宿主机 mitmdump:8443
$ADB -s $DEV shell "iptables -t nat -F CAPCHAIN 2>/dev/null; iptables -t nat -N CAPCHAIN 2>/dev/null; iptables -t nat -A CAPCHAIN -p tcp -d 10.0.2.2 --dport 443 -j DNAT --to-destination 10.0.2.2:8443; iptables -t nat -A OUTPUT -j CAPCHAIN; iptables -t nat -L CAPCHAIN"
echo DONE
