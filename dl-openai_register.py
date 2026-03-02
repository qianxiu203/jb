import json
import os
import re
import sys
import time
import uuid
import math
import random
import string
import secrets
import hashlib
import base64
import threading
import argparse
import itertools
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, quote
from dataclasses import dataclass
from typing import Any, Dict, Optional, List
import urllib.parse
import urllib.request
import urllib.error

try:
    from curl_cffi import requests
    print(f"[*] curl_cffi 版本检查通过")
except ImportError as e:
    print(f"[Error] 缺少 curl_cffi: {e}")
    print("[Fix] 请运行: pip install curl_cffi")
    sys.exit(1)

# ==========================================
# 代理池管理（新增）
# ==========================================

def fetch_us_proxies() -> List[str]:
    """从 jsDelivr 获取美国免费代理列表"""
    url = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/countries/US/data.txt"
    try:
        print(f"[*] 正在下载代理列表: {url}")
        # 注意：这里使用标准requests或urllib，避免curl_cffi可能的问题
        import urllib.request
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read().decode('utf-8')
        
        proxies = []
        for line in data.strip().split('\n'):
            line = line.strip()
            if ':' in line and not line.startswith('#') and not line.startswith('http'):
                # 格式: ip:port -> 转换为 http://ip:port
                proxies.append(f"http://{line}")
        
        print(f"[√] 成功获取 {len(proxies)} 个 US 代理")
        if len(proxies) > 0:
            print(f"[*] 示例代理: {proxies[0]}")
        return proxies
    except Exception as e:
        print(f"[Error] 获取代理列表失败: {e}")
        return []

def check_proxy_usable(proxy: str) -> bool:
    """验证代理是否可用且确实在美国"""
    try:
        print(f"  测试代理 {proxy}...", end="", flush=True)
        # 使用 curl_cffi 测试
        resp = requests.get(
            "https://cloudflare.com/cdn-cgi/trace",
            proxies={"http": proxy, "https": proxy},
            timeout=10,
            impersonate="chrome"
        )
        if resp.status_code == 200:
            loc_match = re.search(r"^loc=(\w+)$", resp.text, re.MULTILINE)
            ip_match = re.search(r"^ip=(.+)$", resp.text, re.MULTILINE)
            if loc_match and ip_match:
                loc = loc_match.group(1)
                ip = ip_match.group(1)
                if loc == "US":
                    print(f" [√] {ip} ({loc})")
                    return True
                else:
                    print(f" [×] 位置不对: {ip} ({loc})")
                    return False
        print(f" [×] 状态码: {resp.status_code}")
        return False
    except Exception as e:
        print(f" [×] 连接失败: {str(e)[:50]}")
        return False

# ==========================================
# Mail.tm 临时邮箱 API（保持原有）
# ==========================================

MAILTM_BASE = "https://api.mail.tm"

def _mailtm_headers(*, token: str = "", use_json: bool = False) -> Dict[str, Any]:
    headers = {"Accept": "application/json"}
    if use_json:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def _mailtm_domains(proxies: Any = None) -> List[str]:
    resp = requests.get(
        f"{MAILTM_BASE}/domains",
        headers=_mailtm_headers(),
        proxies=proxies,
        impersonate="chrome",
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"获取 Mail.tm 域名失败，状态码: {resp.status_code}")

    data = resp.json()
    domains = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("hydra:member") or data.get("items") or []
    else:
        items = []

    for item in items:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "").strip()
        is_active = item.get("isActive", True)
        is_private = item.get("isPrivate", False)
        if domain and is_active and not is_private:
            domains.append(domain)

    return domains

def get_email_and_token(proxies: Any = None) -> tuple:
    """创建 Mail.tm 邮箱并获取 Bearer Token"""
    try:
        domains = _mailtm_domains(proxies)
        if not domains:
            print("[Error] Mail.tm 没有可用域名")
            return "", ""
        domain = random.choice(domains)

        for _ in range(5):
            local = f"oc{secrets.token_hex(5)}"
            email = f"{local}@{domain}"
            password = secrets.token_urlsafe(18)

            create_resp = requests.post(
                f"{MAILTM_BASE}/accounts",
                headers=_mailtm_headers(use_json=True),
                json={"address": email, "password": password},
                proxies=proxies,
                impersonate="chrome",
                timeout=15,
            )

            if create_resp.status_code not in (200, 201):
                continue

            token_resp = requests.post(
                f"{MAILTM_BASE}/token",
                headers=_mailtm_headers(use_json=True),
                json={"address": email, "password": password},
                proxies=proxies,
                impersonate="chrome",
                timeout=15,
            )

            if token_resp.status_code == 200:
                token = str(token_resp.json().get("token") or "").strip()
                if token:
                    return email, token

        print("[Error] Mail.tm 邮箱创建成功但获取 Token 失败")
        return "", ""
    except Exception as e:
        print(f"[Error] 请求 Mail.tm API 出错: {e}")
        return "", ""

def get_oai_code(token: str, email: str, proxies: Any = None) -> str:
    """使用 Mail.tm Token 轮询获取 OpenAI 验证码"""
    url_list = f"{MAILTM_BASE}/messages"
    regex = r"(?<!\d)(\d{6})(?!\d)"
    seen_ids: set[str] = set()

    print(f"[*] 正在等待邮箱 {email} 的验证码...", end="", flush=True)

    for _ in range(40):
        print(".", end="", flush=True)
        try:
            resp = requests.get(
                url_list,
                headers=_mailtm_headers(token=token),
                proxies=proxies,
                impersonate="chrome",
                timeout=15,
            )
            if resp.status_code != 200:
                time.sleep(3)
                continue

            data = resp.json()
            if isinstance(data, list):
                messages = data
            elif isinstance(data, dict):
                messages = data.get("hydra:member") or data.get("messages") or []
            else:
                messages = []

            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                msg_id = str(msg.get("id") or "").strip()
                if not msg_id or msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                read_resp = requests.get(
                    f"{MAILTM_BASE}/messages/{msg_id}",
                    headers=_mailtm_headers(token=token),
                    proxies=proxies,
                    impersonate="chrome",
                    timeout=15,
                )
                if read_resp.status_code != 200:
                    continue

                mail_data = read_resp.json()
                sender = str(
                    ((mail_data.get("from") or {}).get("address") or "")
                ).lower()
                subject = str(mail_data.get("subject") or "")
                intro = str(mail_data.get("intro") or "")
                text = str(mail_data.get("text") or "")
                html = mail_data.get("html") or ""
                if isinstance(html, list):
                    html = "\n".join(str(x) for x in html)
                content = "\n".join([subject, intro, text, str(html)])

                if "openai" not in sender and "openai" not in content.lower():
                    continue

                m = re.search(regex, content)
                if m:
                    print(" 抓到啦! 验证码:", m.group(1))
                    return m.group(1)
        except Exception:
            pass

        time.sleep(3)

    print(" 超时，未收到验证码")
    return ""

# ==========================================
# OAuth 授权与辅助函数（保持原有）
# ==========================================

AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_REDIRECT_URI = f"http://localhost:1455/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"

def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())

def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)

def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)

def _parse_callback_url(callback_url: str) -> Dict[str, Any]:
    candidate = callback_url.strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}

    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate:
            candidate = f"http://{candidate}"
        elif "=" in candidate:
            candidate = f"http://localhost/?{candidate}"

    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)

    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values

    def get1(k: str) -> str:
        v = query.get(k, [""])
        return (v[0] or "").strip()

    code = get1("code")
    state = get1("state")
    error = get1("error")
    error_description = get1("error_description")

    if code and not state and "#" in code:
        code, state = code.split("#", 1)

    if not error and error_description:
        error, error_description = error_description, ""

    return {
        "code": code,
        "state": state,
        "error": error,
        "error_description": error_description,
    }

def _jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        payload = base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii"))
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return {}

def _decode_jwt_segment(seg: str) -> Dict[str, Any]:
    raw = (seg or "").strip()
    if not raw:
        return {}
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode((raw + pad).encode("ascii"))
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}

def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0

def _post_form(url: str, data: Dict[str, str], timeout: int = 30) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError(
                    f"token exchange failed: {resp.status}: {raw.decode('utf-8', 'replace')}"
                )
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise RuntimeError(
            f"token exchange failed: {exc.code}: {raw.decode('utf-8', 'replace')}"
        ) from exc

@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str

def generate_oauth_url(
    *, redirect_uri: str = DEFAULT_REDIRECT_URI, scope: str = DEFAULT_SCOPE
) -> OAuthStart:
    state = _random_state()
    code_verifier = _pkce_verifier()
    code_challenge = _sha256_b64url_no_pad(code_verifier)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return OAuthStart(
        auth_url=auth_url,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )

def submit_callback_url(
    *,
    callback_url: str,
    expected_state: str,
    code_verifier: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> str:
    cb = _parse_callback_url(callback_url)
    if cb["error"]:
        desc = cb["error_description"]
        raise RuntimeError(f"oauth error: {cb['error']}: {desc}".strip())

    if not cb["code"]:
        raise ValueError("callback url missing ?code=")
    if not cb["state"]:
        raise ValueError("callback url missing ?state=")
    if cb["state"] != expected_state:
        raise ValueError("state mismatch")

    token_resp = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": cb["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )

    access_token = (token_resp.get("access_token") or "").strip()
    refresh_token = (token_resp.get("refresh_token") or "").strip()
    id_token = (token_resp.get("id_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))

    claims = _jwt_claims_no_verify(id_token)
    email = str(claims.get("email") or "").strip()
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()

    now = int(time.time())
    expired_rfc3339 = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0))
    )
    now_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    config = {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": now_rfc3339,
        "email": email,
        "type": "codex",
        "expired": expired_rfc3339,
    }

    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))

# ==========================================
# 核心注册逻辑（保持原有）
# ==========================================

def run(proxy: Optional[str]) -> Optional[str]:
    proxies: Any = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    s = requests.Session(proxies=proxies, impersonate="chrome")

    try:
        trace = s.get("https://cloudflare.com/cdn-cgi/trace", timeout=10)
        trace = trace.text
        loc_re = re.search(r"^loc=(.+)$", trace, re.MULTILINE)
        loc = loc_re.group(1) if loc_re else None
        print(f"[*] 当前 IP 所在地: {loc}")
        if loc == "CN" or loc == "HK":
            raise RuntimeError("检查代理哦w - 所在地不支持")
    except Exception as e:
        print(f"[Error] 网络连接检查失败: {e}")
        return None

    email, dev_token = get_email_and_token(proxies)
    if not email or not dev_token:
        return None
    print(f"[*] 成功获取 Mail.tm 邮箱与授权: {email}")

    oauth = generate_oauth_url()
    url = oauth.auth_url

    try:
        resp = s.get(url, timeout=15)
        did = s.cookies.get("oai-did")
        print(f"[*] Device ID: {did}")

        signup_body = f'{{"username":{{"value":"{email}","kind":"email"}},"screen_hint":"signup"}}'
        sen_req_body = f'{{"p":"","id":"{did}","flow":"authorize_continue"}}'

        sen_resp = requests.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=sen_req_body,
            proxies=proxies,
            impersonate="chrome",
            timeout=15,
        )

        if sen_resp.status_code != 200:
            print(f"[Error] Sentinel 异常拦截，状态码: {sen_resp.status_code}")
            return None

        sen_token = sen_resp.json()["token"]
        sentinel = f'{{"p": "", "t": "", "c": "{sen_token}", "id": "{did}", "flow": "authorize_continue"}}'

        signup_resp = s.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "referer": "https://auth.openai.com/create-account",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel,
            },
            data=signup_body,
        )
        print(f"[*] 提交注册表单状态: {signup_resp.status_code}")

        otp_resp = s.post(
            "https://auth.openai.com/api/accounts/passwordless/send-otp",
            headers={
                "referer": "https://auth.openai.com/create-account/password",
                "accept": "application/json",
                "content-type": "application/json",
            },
        )
        print(f"[*] 验证码发送状态: {otp_resp.status_code}")

        code = get_oai_code(dev_token, email, proxies)
        if not code:
            return None

        code_body = f'{{"code":"{code}"}}'
        code_resp = s.post(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            headers={
                "referer": "https://auth.openai.com/email-verification",
                "accept": "application/json",
                "content-type": "application/json",
            },
            data=code_body,
        )
        print(f"[*] 验证码校验状态: {code_resp.status_code}")

        create_account_body = '{"name":"Neo","birthdate":"2000-02-20"}'
        create_account_resp = s.post(
            "https://auth.openai.com/api/accounts/create_account",
            headers={
                "referer": "https://auth.openai.com/about-you",
                "accept": "application/json",
                "content-type": "application/json",
            },
            data=create_account_body,
        )
        create_account_status = create_account_resp.status_code
        print(f"[*] 账户创建状态: {create_account_status}")

        if create_account_status != 200:
            print(create_account_resp.text)
            return None

        auth_cookie = s.cookies.get("oai-client-auth-session")
        if not auth_cookie:
            print("[Error] 未能获取到授权 Cookie")
            return None

        auth_json = _decode_jwt_segment(auth_cookie.split(".")[0])
        workspaces = auth_json.get("workspaces") or []
        if not workspaces:
            print("[Error] 授权 Cookie 里没有 workspace 信息")
            return None
        workspace_id = str((workspaces[0] or {}).get("id") or "").strip()
        if not workspace_id:
            print("[Error] 无法解析 workspace_id")
            return None

        select_body = f'{{"workspace_id":"{workspace_id}"}}'
        select_resp = s.post(
            "https://auth.openai.com/api/accounts/workspace/select",
            headers={
                "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "content-type": "application/json",
            },
            data=select_body,
        )

        if select_resp.status_code != 200:
            print(f"[Error] 选择 workspace 失败，状态码: {select_resp.status_code}")
            print(select_resp.text)
            return None

        continue_url = str((select_resp.json() or {}).get("continue_url") or "").strip()
        if not continue_url:
            print("[Error] workspace/select 响应里缺少 continue_url")
            return None

        current_url = continue_url
        for _ in range(6):
            final_resp = s.get(current_url, allow_redirects=False, timeout=15)
            location = final_resp.headers.get("Location") or ""

            if final_resp.status_code not in [301, 302, 303, 307, 308]:
                break
            if not location:
                break

            next_url = urllib.parse.urljoin(current_url, location)
            if "code=" in next_url and "state=" in next_url:
                return submit_callback_url(
                    callback_url=next_url,
                    code_verifier=oauth.code_verifier,
                    redirect_uri=oauth.redirect_uri,
                    expected_state=oauth.state,
                )
            current_url = next_url

        print("[Error] 未能在重定向链中捕获到最终 Callback URL")
        return None

    except Exception as e:
        print(f"[Error] 运行时发生错误: {e}")
        import traceback
        traceback.print_exc()
        return None

# ==========================================
# 主函数（修复版）
# ==========================================

def main() -> None:
    print("[Debug] 脚本启动...")
    
    parser = argparse.ArgumentParser(description="OpenAI 自动注册脚本（US代理池版）")
    parser.add_argument("--proxy", default=None, help="指定单个代理，如 http://ip:port")
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument("--sleep-min", type=int, default=60, help="成功注册后最短等待秒数")
    parser.add_argument("--sleep-max", type=int, default=120, help="成功注册后最长等待秒数")
    parser.add_argument("--skip-check", action="store_true", help="跳过代理可用性检测")
    parser.add_argument("--test-proxy", action="store_true", help="仅测试代理可用性，不注册")
    
    try:
        args = parser.parse_args()
    except SystemExit as e:
        print(f"[Debug] 参数解析错误: {e}")
        return
    
    print(f"[Info] 参数解析完成")
    print(f"[Info] Mode: {'单次' if args.once else '循环'} | 代理检测: {'跳过' if args.skip_check else '开启'}")

    # 代理管理
    proxy_pool = []
    proxy_iter = None
    
    if args.proxy:
        proxy_pool = [args.proxy]
        print(f"[*] 使用指定代理: {args.proxy}")
    else:
        print("[*] 正在获取 US 免费代理列表...")
        proxy_pool = fetch_us_proxies()
        if not proxy_pool:
            print("[Error] 无法获取代理列表，退出")
            return
        random.shuffle(proxy_pool)
        proxy_iter = itertools.cycle(proxy_pool)
        print(f"[*] 代理池准备完成，共 {len(proxy_pool)} 个")

    # 仅测试代理模式
    if args.test_proxy:
        print("\n[*] 进入代理测试模式...")
        for i, proxy in enumerate(proxy_pool[:10]):  # 测试前10个
            print(f"\n[{i+1}] 测试 {proxy}")
            check_proxy_usable(proxy)
            time.sleep(1)
        return

    count = 0
    failed_proxies = set()
    
    print("\n[*] 开始主循环...")
    
    while True:
        count += 1
        current_proxy = None
        
        # 获取代理
        if args.proxy:
            current_proxy = args.proxy
        else:
            # 找到可用代理
            attempts = 0
            while attempts < len(proxy_pool):
                candidate = next(proxy_iter)
                if candidate in failed_proxies:
                    attempts += 1
                    continue
                
                if not args.skip_check:
                    if check_proxy_usable(candidate):
                        current_proxy = candidate
                        break
                    else:
                        failed_proxies.add(candidate)
                else:
                    current_proxy = candidate
                    break
                attempts += 1
            
            if not current_proxy:
                print("[Error] 代理池暂时耗尽，等待 60 秒后重试...")
                time.sleep(60)
                failed_proxies.clear()
                continue
        
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] >>> 第 {count} 次尝试 <<<")
        print(f"[*] 使用代理: {current_proxy}")
        
        try:
            token_json = run(current_proxy)
            
            if token_json:
                try:
                    t_data = json.loads(token_json)
                    fname_email = t_data.get("email", "unknown").replace("@", "_")
                except Exception:
                    fname_email = "unknown"
                
                file_name = f"token_{fname_email}_{int(time.time())}.json"
                with open(file_name, "w", encoding="utf-8") as f:
                    f.write(token_json)
                print(f"[√] 成功! Token 已保存至: {file_name}")
                
                if args.once:
                    break
                    
                wait_time = random.randint(args.sleep_min, args.sleep_max)
                print(f"[*] 休息 {wait_time} 秒后继续...")
                time.sleep(wait_time)
            else:
                print("[-] 注册失败，更换代理...")
                failed_proxies.add(current_proxy)
                
        except Exception as e:
            print(f"[Error] 主循环异常: {e}")
            import traceback
            traceback.print_exc()
            failed_proxies.add(current_proxy)
            
        if args.once:
            break
            
        time.sleep(random.randint(3, 5))  # 失败间隔

    print("[*] 脚本结束")

if __name__ == "__main__":
    print("[Debug] __main__ 入口")
    try:
        main()
    except Exception as e:
        print(f"[Fatal Error] {e}")
        import traceback
        traceback.print_exc()
