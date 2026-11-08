
import socket, ssl, threading, os, time, struct, datetime
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

CERT_DIR = "C:/Users/devin/mitmcerts"; os.makedirs(CERT_DIR, exist_ok=True)
LOG = open("C:/Users/devin/flows.log", "ab", buffering=0)

CA_KEY = rsa.generate_private_key(65537, 2048)
ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"genv-mitm")])
ca_cert = (x509.CertificateBuilder()
    .subject_name(ca_name).issuer_name(ca_name)
    .public_key(CA_KEY.public_key()).serial_number(1)
    .not_valid_before(datetime.datetime.now(datetime.UTC)-datetime.timedelta(days=1))
    .not_valid_after(datetime.datetime.now(datetime.UTC)+datetime.timedelta(days=3650))
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
    .sign(CA_KEY, hashes.SHA256()))
CERTS = {}
open("C:/Users/devin/ca.pem","wb").write(ca_cert.public_bytes(serialization.Encoding.PEM)+CA_KEY.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))


def cert_ctx(host):
    if host in CERTS: return CERTS[host]
    names=[host]
    if host.count(".")>=2: names.append("*."+host.split(".",1)[1])
    key = rsa.generate_private_key(65537, 2048)
    cert = (x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_cert.subject).public_key(key.public_key())
        .serial_number(int(time.time()*1000)&0x7fffffff)
        .not_valid_before(datetime.datetime.now(datetime.UTC)-datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.UTC)+datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]),False)
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
        .sign(CA_KEY,hashes.SHA256()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    cf=os.path.join(CERT_DIR, host.replace("*","_")+".pem")
    open(cf,"wb").write(cert.public_bytes(serialization.Encoding.PEM)+
        key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
    ctx.load_cert_chain(cf)
    CERTS[host]=ctx
    return ctx

def parse_sni(d):
    try:
        if d[0]!=0x16: return None
        p=5
        # handshake: type(1) len(3) version(2) random(32) sid_len(1)
        p+=1+3+2+32
        sl=d[p]; p+=1+sl
        cs=struct.unpack(">H",d[p:p+2])[0]; p+=2+cs
        cm=d[p]; p+=1+cm
        ext_total=struct.unpack(">H",d[p:p+2])[0]; p+=2
        end=p+ext_total
        while p<end:
            et,el=struct.unpack(">HH",d[p:p+4]); p+=4
            if et==0:
                ln=struct.unpack(">H",d[p+1:p+3])[0]
                nt=d[p+3]
                nl=struct.unpack(">H",d[p+4:p+6])[0]
                return d[p+6:p+6+nl].decode()
            p+=4+el-4+el*0+0 if False else p+el-4+4-p+el*0  # noop
            p+=el-4+4-(p-(p-4)) if False else el-4+4-p
            break
        # fallback simple loop (rewrite below)
    except Exception as ex:
        return None
    return None

def parse_sni2(d):
    try:
        if d[0]!=0x16: return None
        p=5+4+2+32
        sl=d[p]; p+=1+sl
        cs=struct.unpack(">H",d[p:p+2])[0]; p+=2+cs
        cm=d[p]; p+=1+cm
        et=struct.unpack(">H",d[p:p+2])[0]; p+=2
        end=p+et
        while p+4<=end:
            typ,ln=struct.unpack(">HH",d[p:p+4]); p+=4
            if typ==0:
                q=p+2
                q+=1
                nl=struct.unpack(">H",d[q:q+2])[0]; q+=2
                return d[q:q+nl].decode()
            p+=ln
    except Exception:
        return None
    return None

def pipe(a,b,tag,sni):
    try:
        while True:
            d=a.recv(65536)
            if not d: break
            LOG.write(("\n== %s %s ==\n"%(tag,sni)).encode()); LOG.write(d)
            b.sendall(d)
    except: pass
    try: a.close(); b.close()
    except: pass

def handle(c,addr):
    sni=None
    try:
        c.settimeout(15)
        # peek ClientHello
        peek=c.recv(8192, socket.MSG_PEEK)
        sni=parse_sni2(peek) or "unknown"
        # relay non-target domains untouched (keeps system/GMS pinned traffic working)
        TARGET=("geminigen.ai","snapgen.ai","mail.tm","one.one.one.one","jsonplaceholder","icanhazip","pokeapi.co")
        if not any(t in sni for t in TARGET):
            up=socket.create_connection((sni,443),timeout=15)
            print("[r] %s %s"%(addr,sni),flush=True)
            t1=threading.Thread(target=pipe,args=(c,up,"C>S-r",sni),daemon=True)
            t2=threading.Thread(target=pipe,args=(up,c,"S>C-r",sni),daemon=True)
            t1.start(); t2.start(); t1.join(); t2.join()
            return
        ctx=cert_ctx(sni if sni!="unknown" else "api.geminigen.ai")
        ss=ctx.wrap_socket(c,server_side=True)
        up=socket.create_connection((sni,443),timeout=15)
        uctx=ssl.create_default_context(); uctx.check_hostname=False; uctx.verify_mode=ssl.CERT_NONE
        us=uctx.wrap_socket(up,server_hostname=sni)
        print("[+] %s -> %s"%(addr,sni),flush=True)
        t1=threading.Thread(target=pipe,args=(ss,us,"C>S",sni),daemon=True)
        t2=threading.Thread(target=pipe,args=(us,ss,"S>C",sni),daemon=True)
        t1.start(); t2.start(); t1.join(); t2.join()
    except Exception as ex:
        print("[!] %s sni=%s %s"%(addr,sni,ex),flush=True)
    try: c.close()
    except: pass

s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(("0.0.0.0",9443)); s.listen(64)
print("[*] mitm2 on 9443",flush=True)
while True:
    c,a=s.accept()
    threading.Thread(target=handle,args=(c,a),daemon=True).start()
