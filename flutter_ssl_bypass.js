// flutter_ssl_bypass.js
// 1) Patch BoringSSL cert verification inside libflutter.so (Flutter uses its own
//    compiled-in CA roots and ignores the Android system store + proxy).
//    Function: ssl_crypto_x509_session_verify_cert_chain -> return true.
// 2) After verification is bypassed, transparent proxying (iptables DNAT ->
//    mitmproxy) can decrypt all Dart TLS traffic.

function hookVerify(moduleName) {
    var mod = Process.findModuleByName(moduleName);
    if (!mod) { console.log("[-] no module " + moduleName); return false; }
    console.log("[*] scanning " + moduleName + " base=" + mod.base + " size=" + mod.size);

    // Flutter >=3.x arm64 signature for ssl_crypto_x509_session_verify_cert_chain.
    // The function ends with a sequence like:
    //   e0 03 13 aa  mov x0, x19 / cbz ... ret   -- pattern varies; we scan for the
    //   known prologue instead: it is a leaf-ish function referencing
    //   "ssl_crypto_x509" via ADRP+ADD chains.
    // Robust approach: find the function via its error-string references.
    // The function calls ssl_crypto_x509_session_verify... fallback:
    // scan for pattern used by reflutter for arm64:
    var patterns = [
        // reflutter arm64 pattern (Flutter 3.x)
        "FF 43 01 D1 FE 67 01 A9 F8 5F 02 A9 F6 57 03 A9 F4 4F 04 A9 1F 20 03 D5 20 00 80 D2",
        // x86_64 generic prologue variant (for translated/other builds)
        "55 48 89 E5 41 57 41 56 41 55 41 54 53 48 83 EC"
    ];
    var hits = [];
    for (var i = 0; i < patterns.length; i++) {
        try {
            var m = Memory.scanSync(mod.base, mod.size, patterns[i]);
            for (var j = 0; j < m.length; j++) hits.push(m[j].address);
        } catch (e) { }
    }
    console.log("[*] pattern hits: " + hits.length);
    hits.forEach(function (addr) {
        console.log("[+] hooking verify at " + addr);
        try {
            Interceptor.attach(addr, {
                onLeave: function (ret) { ret.replace(0x1); }
            });
        } catch (e) { console.log("hook fail " + e); }
    });
    return hits.length > 0;
}

setTimeout(function () {
    if (!hookVerify("libflutter.so")) {
        console.log("[!] no pattern hit — will retry pattern-free SSL_read/SSL_write dump mode");
    }
}, 3000);
