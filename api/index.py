from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
import requests
import json
import os
import hashlib
import gzip
import time




app = FastAPI()

# 获取当前文件的绝对路径，确保能在 Vercel 环境中找到根目录的 json
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_JSON_FILE = os.path.join(BASE_DIR, "jx3_all_backup.json")

COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#14b8a6", "#f43f5e"]

# ---- CloudBase 缓存配置 (安全脱敏版：从环境变量读取) ----
TCB_ENV_ID = os.environ.get("TCB_ENV_ID", "此处防止本地报错可留空")
TCB_API_KEY = os.environ.get("TCB_API_KEY", "此处防止本地报错可留空")

TCB_BASE_URL = f"https://{TCB_ENV_ID}.api.tcloudbasegateway.com"
TCB_COLLECTION_URL = f"{TCB_BASE_URL}/v1/database/instances/(default)/databases/(default)/collections/html_cache"
TCB_USERS_URL = f"{TCB_BASE_URL}/v1/database/instances/(default)/databases/(default)/collections/users"
TCB_JX3IDS_URL = f"{TCB_BASE_URL}/v1/database/instances/(default)/databases/(default)/collections/user_jx3ids"
TCB_QUERY_LOGS_URL = f"{TCB_BASE_URL}/v1/database/instances/(default)/databases/(default)/collections/query_logs"

TCB_HEADERS = {
    "Authorization": f"Bearer {TCB_API_KEY}",
    "Content-Type": "application/json",
}

# ---- 管理员白名单 (从环境变量动态解析，支持多个邮箱用逗号分隔) ----
admin_emails_raw = os.environ.get("ADMIN_EMAILS", "")
ADMIN_EMAILS = [email.strip() for email in admin_emails_raw.split(",") if email.strip()]


def generate_progress_bars(total, user_counts, short_ids, display_names=None):
    html = ""
    for idx, sid in enumerate(short_ids):
        count = user_counts.get(sid, 0)
        pct = round((count / total * 100), 1) if total > 0 else 0
        color = COLORS[idx % len(COLORS)]
        label = (display_names or {}).get(sid, sid)
        text = f"{label}: {pct}% ({count}/{total})" if pct > 15 else f"{pct}%"

        html += f"""
        <div class="progress-wrapper" title="账号 {label} 进度: {count}/{total}">
            <div class="progress-fill" style="width: {pct}%; background-color: {color};">
                <span class="progress-text">{text}</span>
            </div>
        </div>
        """
    return html


# 首页：高颜值重构版
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    session_user = request.cookies.get("session_user", "")

    # 未登录状态的 HTML 模板
    unlogged_html = """
    <div class="auth-card">
        <h3 class="section-title">🔑 账号登录 / 注册</h3>
        <div class="input-group">
            <input type="text" id="login-user" placeholder="邮箱地址">
        </div>
        <div class="input-group">
            <input type="password" id="login-pass" placeholder="账户密码">
        </div>
        <div class="row-btns">
            <button class="btn btn-outline" onclick="doRegister()">注册新账号</button>
            <button class="btn btn-primary" onclick="doLogin()">立即登录</button>
        </div>
    </div>
    """

    # 已登录状态的 HTML 模板
    logged_html = f"""
    <div class="auth-card">
        <div class="user-header">
            <div class="user-info">👤 欢迎回来：<b>{session_user}</b></div>
            <span class="logout-btn" onclick="logout()">[退出登录]</span>
        </div>
        <hr class="divider">
        <h3 class="section-title" style="margin-top:0;">🔖 快捷账号备注</h3>
        <p class="subtitle">给你的 UID 写上备注，看板将直接显示角色名。</p>
        <div class="uid-input-row">
            <input type="text" id="uid-input" placeholder="剑网3 UID" class="flex-2">
            <input type="text" id="alias-input" placeholder="备注名 (如:大号剑纯)" class="flex-3">
            <button class="btn btn-success flex-1" onclick="saveUid()">保存</button>
        </div>
        <div class="uid-list" id="uid-list">
            <div style="color:#94a3b8; font-size:13px; text-align:center; padding: 10px;">正在加载账号...</div>
        </div>
    </div>
    """

    auth_section = logged_html if session_user else unlogged_html
    init_script = "loadUids();" if session_user else ""

    return f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>剑网3 多角色成就查询</title>
        <style>
            :root {{ --bg: #f1f5f9; --card: #ffffff; --primary: #3b82f6; --primary-hover: #2563eb; --success: #10b981; --success-hover: #059669; --text-main: #1e293b; --text-sub: #64748b; --border: #e2e8f0; }}
            body {{ font-family: 'Segoe UI', -apple-system, sans-serif; background: var(--bg); display: flex; justify-content: center; min-height: 100vh; margin: 0; padding: 40px 20px; }}
            .main-container {{ max-width: 560px; width: 100%; }}
            .page-title {{ text-align: center; color: var(--text-main); margin-bottom: 30px; font-size: 28px; font-weight: 800; text-shadow: 0 1px 2px rgba(0,0,0,0.05); }}

            /* 卡片统一样式 */
            .card {{ background: var(--card); padding: 30px; border-radius: 16px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05), 0 2px 4px -1px rgba(0,0,0,0.03); margin-bottom: 24px; }}
            .section-title {{ color: var(--text-main); font-size: 18px; margin-bottom: 16px; margin-top: 0; }}
            .subtitle {{ color: var(--text-sub); font-size: 13px; margin-top: -10px; margin-bottom: 16px; }}

            /* 表单与按钮 */
            .input-group {{ margin-bottom: 16px; }}
            input[type="text"], input[type="password"] {{ width: 100%; padding: 12px 16px; border: 2px solid var(--border); border-radius: 10px; font-size: 15px; outline: none; box-sizing: border-box; transition: all 0.2s; background: #f8fafc; }}
            input[type="text"]:focus, input[type="password"]:focus {{ border-color: var(--primary); background: #fff; box-shadow: 0 0 0 3px rgba(59,130,246,0.15); }}

            .row-btns {{ display: flex; gap: 12px; }}
            .btn {{ padding: 12px 20px; border-radius: 10px; font-size: 15px; cursor: pointer; font-weight: bold; border: none; transition: 0.2s; display: inline-block; text-align: center; box-sizing: border-box; }}
            .btn-primary {{ background: var(--primary); color: white; width: 100%; box-shadow: 0 4px 6px -1px rgba(59,130,246,0.3); }}
            .btn-primary:hover {{ background: var(--primary-hover); transform: translateY(-1px); }}
            .btn-success {{ background: var(--success); color: white; }}
            .btn-success:hover {{ background: var(--success-hover); }}
            .btn-outline {{ background: white; border: 2px solid var(--border); color: var(--text-main); width: 100%; }}
            .btn-outline:hover {{ border-color: #cbd5e1; background: #f8fafc; }}

            /* 已登录区域专属 */
            .user-header {{ display: flex; justify-content: space-between; align-items: center; background: #eff6ff; padding: 12px 16px; border-radius: 10px; color: #1e3a8a; }}
            .logout-btn {{ color: #ef4444; font-size: 13px; cursor: pointer; font-weight: bold; }}
            .divider {{ border: none; border-top: 1px dashed var(--border); margin: 24px 0; }}
            .uid-input-row {{ display: flex; gap: 8px; margin-bottom: 16px; }}
            .flex-1 {{ flex: 1; }} .flex-2 {{ flex: 2; }} .flex-3 {{ flex: 3; }}
            .uid-list {{ display: flex; flex-direction: column; gap: 8px; }}
            .uid-item {{ background: #f8fafc; border: 1px solid var(--border); padding: 10px 14px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; font-size: 14px; }}
            .uid-item b {{ color: var(--primary); }}
            .del-btn {{ background: #fee2e2; color: #ef4444; border-radius: 4px; padding: 4px 8px; font-size: 12px; cursor: pointer; font-weight: bold; }}
            .del-btn:hover {{ background: #fecaca; }}

            /* 主查询区 */
            .generate-card {{ background: linear-gradient(145deg, #ffffff, #f8fafc); border: 2px solid var(--primary); }}
            .massive-btn {{ background: linear-gradient(135deg, #3b82f6, #2563eb); font-size: 18px; padding: 16px; margin-top: 10px; }}
            .tip-text {{ font-size: 13px; color: var(--text-sub); text-align: center; margin-top: 16px; }}
            .msg-box {{ text-align: center; font-size: 14px; font-weight: bold; margin-bottom: 16px; min-height: 20px; }}
        </style>
    </head>
    <body>
        <div class="main-container">
            <h1 class="page-title">🏆 剑网3 成就看板</h1>

            <div id="msg" class="msg-box"></div>

            {auth_section}

            <div class="card generate-card">
                <h3 class="section-title">✨ 生成专属成就看板</h3>
                <form action="/generate" method="post">
                    <div class="input-group">
                        <input type="text" name="jx3ids" placeholder="输入 JX3ID (多个账号请用空格隔开)" required autocomplete="off">
                    </div>
                    <button type="submit" class="btn btn-primary massive-btn">🚀 一键生成看板</button>
                </form>
                <div class="tip-text">查询过程需要拉取官方实时数据，提交后请耐心等待 3~8 秒。</div>
            </div>
        </div>

        <script>
            const msgBox = document.getElementById('msg');
            const showMsg = (text, color) => {{ msgBox.innerText = text; msgBox.style.color = color || '#10b981'; }};

            async function doRegister() {{
                const u = document.getElementById('login-user').value.trim();
                const p = document.getElementById('login-pass').value.trim();
                if(!u||!p) return showMsg('请填写邮箱和密码', '#ef4444');
                const r = await fetch('/api/register', {{ method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{email:u,password:p}}) }});
                const d = await r.json();
                showMsg(d.detail || '注册成功', r.ok ? '#10b981' : '#ef4444');
                if(r.ok) setTimeout(()=>location.reload(), 800);
            }}

            async function doLogin() {{
                const u = document.getElementById('login-user').value.trim();
                const p = document.getElementById('login-pass').value.trim();
                if(!u||!p) return showMsg('请填写邮箱和密码', '#ef4444');
                const r = await fetch('/api/login', {{ method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{email:u,password:p}}) }});
                const d = await r.json();
                showMsg(d.detail || '登录成功', r.ok ? '#10b981' : '#ef4444');
                if(r.ok) setTimeout(()=>location.reload(), 500);
            }}

            function logout() {{ document.cookie = 'session_user=; path=/; max-age=0'; location.reload(); }}

            async function saveUid() {{
                const uid = document.getElementById('uid-input').value.trim();
                const alias = document.getElementById('alias-input').value.trim();
                if(!uid||!alias) return showMsg('请填写 JX3ID 和备注名', '#ef4444');
                const r = await fetch('/api/save_uid', {{ method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{jx3_uid:uid,alias_name:alias}}) }});
                const d = await r.json();
                showMsg(d.detail || '保存成功', r.ok ? '#10b981' : '#ef4444');
                if(r.ok) {{ document.getElementById('uid-input').value=''; document.getElementById('alias-input').value=''; loadUids(); }}
            }}

            async function loadUids() {{
                const r = await fetch('/api/my_uids');
                const data = await r.json();
                const list = document.getElementById('uid-list');
                if(!data.uids || !data.uids.length) {{ list.innerHTML = '<div style="color:#94a3b8; font-size:13px; text-align:center;">暂无保存的 UID 备注</div>'; return; }}
                list.innerHTML = data.uids.map(x => `
                    <div class="uid-item">
                        <span>UID: ${{x.jx3_uid}} <span style="color:#cbd5e1; margin:0 6px;">|</span> 备注: <b>${{x.alias_name}}</b></span>
                        <span class="del-btn" onclick="delUid('${{x.jx3_uid}}')">删除</span>
                    </div>
                `).join('');
            }}

            async function delUid(uid) {{
                await fetch('/api/del_uid?jx3_uid='+uid, {{ method:'DELETE' }});
                loadUids();
            }}

            {init_script}
        </script>
    </body>
    </html>
    """


# ---- 账户体系 API ----
@app.post("/api/register")
async def api_register(request: Request):
    try:
        body = await request.json()
        email = body.get("email", "").strip()
        password = body.get("password", "").strip()
        if not email or not password:
            return {"detail": "邮箱和密码不能为空"}
        import hashlib as _h
        pw_hash = _h.sha256(password.encode()).hexdigest()
        # 检查是否已存在（加 limit 防 TCB 忽略 where）
        check = requests.get(
            f"{TCB_USERS_URL}/documents",
            params={"where": json.dumps({"email": email}), "limit": 100},
            headers=TCB_HEADERS, timeout=10
        )
        if check.status_code == 200:
            body_c = check.json()
            docs = body_c.get("list") or body_c.get("data") or []
            if isinstance(docs, list):
                for d in docs:
                    if d.get("email") == email:
                        return {"detail": "邮箱已注册"}
        # 插入
        resp = requests.post(
            f"{TCB_USERS_URL}/documents",
            json={"data": [{"email": email, "password": pw_hash}]},
            headers=TCB_HEADERS, timeout=10
        )
        if resp.status_code in (200, 201):
            response = JSONResponse({"detail": "注册成功"})
            response.set_cookie("session_user", email, path="/", max_age=86400*30)
            return response
        return {"detail": f"注册失败: {resp.text[:200]}"}
    except Exception as e:
        return {"detail": f"服务器错误: {str(e)}"}


@app.post("/api/login")
async def api_login(request: Request):
    try:
        body = await request.json()
        email = body.get("email", "").strip()
        password = body.get("password", "").strip()
        if not email or not password:
            return {"detail": "邮箱和密码不能为空"}
        import hashlib as _h
        pw_hash = _h.sha256(password.encode()).hexdigest()
        resp = requests.get(
            f"{TCB_USERS_URL}/documents",
            params={"where": json.dumps({"email": email}), "limit": 100},
            headers=TCB_HEADERS, timeout=10
        )
        if resp.status_code != 200:
            return {"detail": "数据库查询失败"}
        body_r = resp.json()
        docs = body_r.get("list") or body_r.get("data") or []
        if not isinstance(docs, list) or len(docs) == 0:
            return {"detail": "邮箱未注册"}
        # 二次精确遍历，防止 TCB 忽略 where 条件
        user = None
        for d in docs:
            if d.get("email") == email:
                user = d
                break
        if not user:
            return {"detail": "邮箱未注册"}
        if user.get("password") != pw_hash:
            return {"detail": "密码错误"}
        response = JSONResponse({"detail": "登录成功"})
        response.set_cookie("session_user", email, path="/", max_age=86400*30)
        return response
    except Exception as e:
        return {"detail": f"服务器错误: {str(e)}"}


@app.post("/api/save_uid")
async def api_save_uid(request: Request):
    session_user = request.cookies.get("session_user", "")
    if not session_user:
        return {"detail": "请先登录"}
    try:
        body = await request.json()
        jx3_uid = body.get("jx3_uid", "").strip()
        alias_name = body.get("alias_name", "").strip()
        if not jx3_uid or not alias_name:
            return {"detail": "JX3ID 和备注名不能为空"}
        # 拉取该用户所有记录，客户端二次匹配
        check = requests.get(
            f"{TCB_JX3IDS_URL}/documents",
            params={"where": json.dumps({"email": session_user}), "limit": 100},
            headers=TCB_HEADERS, timeout=10
        )
        existing_id = None
        if check.status_code == 200:
            body_c = check.json()
            docs = body_c.get("list") or body_c.get("data") or []
            if isinstance(docs, list):
                for d in docs:
                    if d.get("email") == session_user and d.get("jx3_uid") == jx3_uid:
                        existing_id = d.get("_id") or d.get("id")
                        break
        if existing_id:
            # 更新
            resp = requests.patch(
                f"{TCB_JX3IDS_URL}/documents/{existing_id}",
                json={"data": {"alias_name": alias_name}},
                headers=TCB_HEADERS, timeout=10
            )
        else:
            # 新增
            resp = requests.post(
                f"{TCB_JX3IDS_URL}/documents",
                json={"data": [{"email": session_user, "jx3_uid": jx3_uid, "alias_name": alias_name}]},
                headers=TCB_HEADERS, timeout=10
            )
        if resp.status_code in (200, 201):
            return {"detail": "保存成功"}
        return {"detail": f"保存失败: {resp.text[:200]}"}
    except Exception as e:
        return {"detail": f"服务器错误: {str(e)}"}


@app.get("/api/my_uids")
async def api_my_uids(request: Request):
    session_user = request.cookies.get("session_user", "")
    if not session_user:
        return {"uids": []}
    try:
        resp = requests.get(
            f"{TCB_JX3IDS_URL}/documents",
            params={"where": json.dumps({"email": session_user}), "limit": 100},
            headers=TCB_HEADERS, timeout=10
        )
        if resp.status_code == 200:
            body = resp.json()
            docs = body.get("list") or body.get("data") or []
            if isinstance(docs, list):
                result = []
                for d in docs:
                    if d.get("email") == session_user:
                        result.append({"jx3_uid": d.get("jx3_uid"), "alias_name": d.get("alias_name")})
                return {"uids": result}
        return {"uids": []}
    except Exception:
        return {"uids": []}


@app.delete("/api/del_uid")
async def api_del_uid(request: Request, jx3_uid: str = ""):
    session_user = request.cookies.get("session_user", "")
    if not session_user:
        return {"detail": "请先登录"}
    try:
        check = requests.get(
            f"{TCB_JX3IDS_URL}/documents",
            params={"where": json.dumps({"email": session_user}), "limit": 100},
            headers=TCB_HEADERS, timeout=10
        )
        if check.status_code == 200:
            body_c = check.json()
            docs = body_c.get("list") or body_c.get("data") or []
            if isinstance(docs, list):
                for d in docs:
                    if d.get("email") == session_user and d.get("jx3_uid") == jx3_uid:
                        doc_id = d.get("_id") or d.get("id")
                        requests.delete(
                            f"{TCB_JX3IDS_URL}/documents/{doc_id}",
                            headers=TCB_HEADERS, timeout=10
                        )
                        break
        return {"detail": "已删除"}
    except Exception as e:
        return {"detail": f"删除失败: {str(e)}"}


# ---- 可复用渲染函数：根据完成数据重新生成完整 HTML ----
def build_dashboard_html(short_ids, user_completed_map, display_names=None):
    """阶段二~四：读取本地 JSON → 统计进度 → 组装前端 HTML"""
    import hashlib as _hashlib
    dn = display_names or {}

    # ---- 阶段二：读取本地数据库 ----
    LOCAL_GZ_FILE = os.path.join(BASE_DIR, "jx3_all_backup.json.gz")
    if not os.path.exists(LOCAL_GZ_FILE):
        return "<h3>服务器配置错误：找不到全量数据库文件。</h3>"

    with gzip.open(LOCAL_GZ_FILE, "rt", encoding="utf-8") as f:
        full_data_list = json.load(f)

    # ---- 阶段三：统计进度与构建HTML行 ----
    stats = {}
    html_templates = {}

    for category in full_data_list:
        m1 = category.get("一级菜单", "未知")
        m2 = category.get("二级菜单", "未知")
        ach_list = category.get("数据", [])

        if m1 not in stats:
            stats[m1] = {"total": 0, "users": {s: 0 for s in short_ids}, "subs": {}}
        if m2 not in stats[m1]["subs"]:
            stats[m1]["subs"][m2] = {"total": 0, "users": {s: 0 for s in short_ids}}

        template_id = "tpl_" + _hashlib.md5(f"{m1}_{m2}".encode('utf-8')).hexdigest()
        if template_id not in html_templates:
            html_templates[template_id] = ""

        for ach in ach_list:
            ach_id = ach.get("ID")
            stats[m1]["total"] += 1
            stats[m1]["subs"][m2]["total"] += 1

            post_data = ach.get("post") or {}
            scene = ach.get("SceneName") or "无"
            layer = ach.get("LayerName") or "无"
            level_val = post_data.get("level", "无")
            content = post_data.get("content") or ""

            tr_data_attrs = f"data-scene='{scene}' data-layer='{layer}' data-level='{level_val}' "
            td_status_html = ""

            for sid in short_ids:
                is_completed = ach_id in user_completed_map[sid]
                status_val = "yes" if is_completed else "no"
                tr_data_attrs += f"data-status-{sid}='{status_val}' "
                if is_completed:
                    stats[m1]["users"][sid] += 1
                    stats[m1]["subs"][m2]["users"][sid] += 1
                    td_status_html += f"<td><button class='badge badge-status badge-status-yes' data-uid='{sid}' data-val='yes'>✅ {dn.get(sid, sid)}已完</button></td>"
                else:
                    td_status_html += f"<td><button class='badge badge-status badge-status-no' data-uid='{sid}' data-val='no'>❌ {dn.get(sid, sid)}未完</button></td>"

            scene_html = f"<button class='badge badge-scene' data-val='{scene}'>{scene}</button>" if scene != "无" else "-"
            layer_html = f"<button class='badge badge-layer' data-val='{layer}'>{layer}</button>" if layer != "无" else "-"
            level_html = f"<button class='badge badge-level' data-val='{level_val}'>{level_val}</button>" if str(level_val) != "无" else "-"
            guide_html = f"""<details><summary>💡 查看攻略详情</summary><div class="guide-content">{content}</div></details>""" if content.strip() else ""

            html_templates[template_id] += f"""
            <tr {tr_data_attrs}>
                {td_status_html}
                <td>{scene_html}</td>
                <td>{layer_html}</td>
                <td>{level_html}</td>
                <td>
                    <div class="ach-title">{ach.get("Name", "未知")}</div>
                    <div class="ach-id">ID: {ach_id}</div>
                </td>
                <td><div>{ach.get("ShortDesc", "")}</div>{guide_html}</td>
                <td>{ach.get("Point", 0)}</td>
            </tr>
            """

    # ---- 阶段四：组装前端代码 ----
    status_filters_html = ""
    for sid in short_ids:
        label = dn.get(sid, sid)
        status_filters_html += f"""
        <div class="filter-group">
            <label>状态({label}):</label>
            <div class="select-wrapper">
                <select id="filter-status-{sid}" class="filter-select">
                    <option value="all">全部</option>
                    <option value="yes">✅ 已完</option>
                    <option value="no">❌ 未完</option>
                </select>
                <button class="clear-single" data-target="filter-status-{sid}">✖</button>
            </div>
        </div>
        """

    dashboard_html = ""
    for m1, m1_data in stats.items():
        m1_hash = _hashlib.md5(m1.encode('utf-8')).hexdigest()
        m1_progress = generate_progress_bars(m1_data["total"], m1_data["users"], short_ids, dn)

        subs_html = f'<div id="subs_{m1_hash}" class="m2-grid" style="display:none;">'
        for m2, m2_data in m1_data["subs"].items():
            tpl_id = "tpl_" + _hashlib.md5(f"{m1}_{m2}".encode('utf-8')).hexdigest()
            m2_progress = generate_progress_bars(m2_data["total"], m2_data["users"], short_ids, dn)
            subs_html += f"""
                <div class="menu-card m2-card" onclick="openTable('{tpl_id}', '{m1} > {m2}')">
                    <div class="menu-title">{m2} <span class="total-count">({m2_data["total"]}项)</span></div>
                    <div class="bars-container">{m2_progress}</div>
                </div>
            """
        subs_html += "</div>"

        dashboard_html += f"""
        <div class="m1-group">
            <div class="menu-card m1-card" onclick="toggleSubs('subs_{m1_hash}')">
                <div class="menu-title">📁 {m1} <span class="total-count">({m1_data["total"]}项)</span> <span class="toggle-tip">展开/折叠 ▼</span></div>
                <div class="bars-container">{m1_progress}</div>
            </div>
            {subs_html}
        </div>
        """

    templates_html = "".join(
        [f'<template id="{tpl_id}">{tr}</template>\n' for tpl_id, tr in html_templates.items()])
    status_th_html = "".join([f"<th width='100px'>状态({dn.get(sid, sid)})</th>" for sid in short_ids])

    final_html = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>剑网3成就 - 动态多维看板</title>
        <style>
            :root {{ --bg: #f8fafc; --card: #ffffff; --border: #e2e8f0; --text: #334155; --primary: #475569; }}
            body {{ font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 20px; line-height: 1.6;}}
            h1 {{ text-align: center; color: #1e293b; margin-bottom: 30px; }}
            .m1-group {{ margin-bottom: 15px; }}
            .menu-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; cursor: pointer; transition: all 0.2s; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
            .menu-card:hover {{ box-shadow: 0 4px 12px rgba(0,0,0,0.1); transform: translateY(-2px); border-color: #cbd5e1; }}
            .m1-card {{ border-left: 5px solid #475569; }}
            .m2-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; padding: 12px 0 10px 30px; border-left: 2px dashed #cbd5e1; margin-left: 10px; }}
            .m2-card {{ border-left: 4px solid #94a3b8; background: #fafaf9; }}
            .menu-title {{ font-size: 1.1em; font-weight: bold; margin-bottom: 10px; color: #0f172a; display: flex; align-items: center; justify-content: space-between; }}
            .total-count {{ font-size: 0.8em; color: #64748b; font-weight: normal; margin-left: 8px; }}
            .toggle-tip {{ font-size: 0.75em; color: #94a3b8; margin-left: auto; }}
            .bars-container {{ display: flex; flex-direction: column; gap: 6px; }}
            .progress-wrapper {{ background: #e2e8f0; border-radius: 6px; height: 22px; width: 100%; position: relative; overflow: hidden; }}
            .progress-fill {{ height: 100%; position: absolute; left: 0; top: 0; display: flex; align-items: center; transition: width 0.8s ease-in-out; }}
            .progress-text {{ font-size: 12px; color: white; font-weight: bold; text-shadow: 1px 1px 2px rgba(0,0,0,0.4); margin-left: 8px; white-space: nowrap; }}
            #view-table {{ display: none; }}
            .table-header-bar {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 15px; padding: 15px; background: var(--card); border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);}}
            .btn-back {{ padding: 8px 16px; background: #3b82f6; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; transition: 0.2s; }}
            .btn-back:hover {{ background: #2563eb; }}
            .filter-bar {{ display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 20px; padding: 15px; background: var(--card); border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); align-items: center; }}
            .filter-group {{ display: flex; align-items: center; gap: 8px; font-weight: bold; font-size: 0.9em; color: var(--primary); }}
            .select-wrapper {{ display: flex; align-items: center; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; background: #fff; }}
            .select-wrapper select {{ border: none; outline: none; padding: 6px 10px; background: transparent; cursor: pointer; font-family: inherit; }}
            .clear-single {{ background: #f8fafc; color: #94a3b8; border: none; border-left: 1px solid var(--border); padding: 7px 10px; cursor: pointer; font-size: 12px; }}
            .clear-single:hover {{ background: #fee2e2; color: #ef4444; }}
            .filter-search-input {{ padding: 7px 12px; border: 1px solid var(--border); border-radius: 6px; outline: none; width: 200px; }}
            table {{ width: 100%; border-collapse: collapse; background: var(--card); border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }}
            th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid var(--border); vertical-align: middle; }}
            th {{ background: #f1f5f9; font-weight: 600; white-space: nowrap; position: sticky; top: 0; z-index: 10; }}
            tr {{ content-visibility: auto; contain-intrinsic-size: 80px; }}
            tr:hover {{ background-color: #f8fafc; }}
            .badge {{ border: none; display: inline-block; padding: 4px 8px; font-size: 0.85em; font-weight: bold; border-radius: 6px; margin: 2px; white-space: nowrap; cursor: pointer; font-family: inherit; }}
            .badge-status-yes {{ background-color: #dcfce7; color: #15803d; }}
            .badge-status-no {{ background-color: #fee2e2; color: #b91c1c; }}
            .badge-scene {{ background-color: #dbeafe; color: #1d4ed8; text-decoration: underline; cursor: default; }}
            .badge-layer {{ background-color: #f3e8ff; color: #7c3aed; cursor: default; }}
            .badge-level {{ background-color: #fef3c7; color: #b45309; cursor: default; }}
            .ach-title {{ font-weight: bold; font-size: 0.95em; margin-bottom: 2px; }}
            .ach-id {{ font-size: 0.8em; color: #94a3b8; }}
            details {{ margin-top: 6px; }}
            summary {{ font-size: 0.9em; color: #0284c7; cursor: pointer; outline: none; }}
            .guide-content {{ margin-top: 8px; font-size: 0.9em; color: #475569; padding-top: 8px; border-top: 1px dashed var(--border); }}
            .guide-content img {{ max-width: 100%; border-radius: 4px; }}
            .global-search-bar {{ padding: 0 0 20px 0; position: relative; }}
            .global-search-input {{ width: 100%; padding: 14px 18px; font-size: 16px; border: 2px solid #e2e8f0; border-radius: 12px; outline: none; box-sizing: border-box; background: var(--card); transition: all 0.2s; }}
            .global-search-input:focus {{ border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,0.15); }}
            .m2-card.search-highlight {{ background: #fef3c7 !important; border-color: #f59e0b !important; box-shadow: 0 0 0 3px rgba(245,158,11,0.25); }}
        </style>
    </head>
    <body>
        <h1>🏆 剑网3 多角色成就看板</h1>
        <div class="global-search-bar"><input type="text" id="global-search" class="global-search-input" placeholder="🔍 全局搜索成就名或场景名（如"战宝军械库"）..."></div>
        <div id="view-menus">{dashboard_html}</div>
        <div id="view-table">
            <div class="table-header-bar">
                <button class="btn-back" onclick="closeTable()">🔙 返回看板</button>
                <h2 id="current-table-title" style="margin:0; color:#334155;">-</h2>
                <div style="width: 100px;"></div>
            </div>
            <div class="filter-bar">
                {status_filters_html}
                <div class="filter-group">
                    <label>场景:</label>
                    <div class="select-wrapper">
                        <select id="filter-scene" class="filter-select"><option value="all">全部</option></select>
                        <button class="clear-single" data-target="filter-scene">✖</button>
                    </div>
                </div>
                <div class="filter-group">
                    <label>层级:</label>
                    <div class="select-wrapper">
                        <select id="filter-layer" class="filter-select"><option value="all">全部</option></select>
                        <button class="clear-single" data-target="filter-layer">✖</button>
                    </div>
                </div>
                <div class="filter-group">
                    <label>难度:</label>
                    <div class="select-wrapper">
                        <select id="filter-level" class="filter-select"><option value="all">全部</option></select>
                        <button class="clear-single" data-target="filter-level">✖</button>
                    </div>
                </div>
                <div class="filter-group" style="margin-left: auto;">
                    <input type="text" id="filter-search" class="filter-search-input" placeholder="🔍 搜索成就名或ID...">
                </div>
            </div>
            <table>
                <thead>
                    <tr>
                        {status_th_html}
                        <th width="100px">场景</th>
                        <th width="100px">层级</th>
                        <th width="60px">难度</th>
                        <th width="200px">成就信息</th>
                        <th>达成条件 & 攻略</th>
                        <th width="60px">资历</th>
                    </tr>
                </thead>
                <tbody id="table-body"></tbody>
            </table>
        </div>
        <div id="templates-pool" style="display:none;">{templates_html}</div>
        <script>
            const userIds = {json.dumps(short_ids)};
            let currentRows = [];
            function toggleSubs(subsId) {{
                const el = document.getElementById(subsId);
                el.style.display = el.style.display === 'none' ? 'grid' : 'none';
            }}
            function openTable(tplId, titleStr) {{
                sessionStorage.setItem('jx3_scrollY', window.scrollY);
                document.getElementById('view-menus').style.display = 'none';
                document.getElementById('view-table').style.display = 'block';
                document.getElementById('current-table-title').innerText = '当前分类：' + titleStr;

                const tbody = document.getElementById('table-body');
                const tpl = document.getElementById(tplId);
                tbody.innerHTML = tpl ? tpl.innerHTML : '<tr><td colspan="10">无数据</td></tr>';

                currentRows = Array.from(tbody.querySelectorAll("tr"));
                const sets = {{ scene: new Set(), layer: new Set(), level: new Set() }};
                currentRows.forEach(row => {{
                    if (row.dataset.scene && row.dataset.scene !== "无") sets.scene.add(row.dataset.scene);
                    if (row.dataset.layer && row.dataset.layer !== "无") sets.layer.add(row.dataset.layer);
                    if (row.dataset.level && row.dataset.level !== "无") sets.level.add(row.dataset.level);
                }});

                ['filter-scene', 'filter-layer', 'filter-level'].forEach(id => {{
                    const key = id.replace('filter-', '');
                    const sel = document.getElementById(id);
                    sel.innerHTML = '<option value="all">全部</option>';
                    Array.from(sets[key]).sort().forEach(v => sel.innerHTML += `<option value="${{v}}">${{v}}</option>`);
                }});
                applyFilters();
                window.scrollTo({{ top: 0, behavior: 'smooth' }});
            }}
            function closeTable() {{
                const searchInput = document.getElementById('global-search');
                if (searchInput) searchInput.value = '';
                document.getElementById('view-table').style.display = 'none';
                document.getElementById('view-menus').style.display = 'block';
                document.getElementById('table-body').innerHTML = '';
                const savedY = sessionStorage.getItem('jx3_scrollY');
                if (savedY !== null) {{
                    requestAnimationFrame(() => {{
                        window.scrollTo({{ top: parseInt(savedY, 10), behavior: 'instant' }});
                    }});
                }}
            }}
            function applyFilters() {{
                const filters = {{
                    scene: document.getElementById("filter-scene").value,
                    layer: document.getElementById("filter-layer").value,
                    level: document.getElementById("filter-level").value,
                }};
                const searchV = document.getElementById("filter-search").value.toLowerCase();
                requestAnimationFrame(() => {{
                    currentRows.forEach(row => {{
                        let match = true;
                        for (let uid of userIds) {{
                            const statusVal = document.getElementById("filter-status-" + uid).value;
                            if (statusVal !== "all" && row.getAttribute("data-status-" + uid) !== statusVal) {{
                                match = false; break;
                            }}
                        }}
                        if (match) {{
                            for (const [key, val] of Object.entries(filters)) {{
                                if (val !== "all" && row.dataset[key] !== val) {{ match = false; break; }}
                            }}
                        }}
                        if (match && searchV) {{
                            const text = row.querySelector('.ach-title').innerText.toLowerCase() + " " + row.querySelector('.ach-id').innerText;
                            if (!text.includes(searchV)) match = false;
                        }}
                        row.style.display = match ? "" : "none";
                    }});
                }});
            }}
            document.querySelectorAll(".filter-select").forEach(el => el.addEventListener("change", applyFilters));
            let searchTimeout = null;
            document.getElementById("filter-search").addEventListener("input", () => {{
                clearTimeout(searchTimeout);
                searchTimeout = setTimeout(applyFilters, 300);
            }});
            document.querySelectorAll(".clear-single").forEach(btn => {{
                btn.addEventListener("click", (e) => {{
                    document.getElementById(e.currentTarget.dataset.target).value = "all";
                    applyFilters();
                }});
            }});
            function initGlobalSearch() {{
                const input = document.getElementById('global-search');
                if (!input) return;
                input.addEventListener('input', function() {{
                    const keyword = this.value.trim();
                    const tableBody = document.getElementById('table-body');
                    const viewMenus = document.getElementById('view-menus');
                    const viewTable = document.getElementById('view-table');
                    const titleEl = document.getElementById('current-table-title');

                    if (!keyword) {{
                        viewTable.style.display = 'none';
                        viewMenus.style.display = 'block';
                        tableBody.innerHTML = '';
                        return;
                    }}

                    const matchedRows = [];
                    document.querySelectorAll('#templates-pool template').forEach(tpl => {{
                        tpl.content.querySelectorAll('tr').forEach(tr => {{
                            const scene = tr.dataset.scene || '';
                            const achTitle = tr.querySelector('.ach-title');
                            const title = achTitle ? achTitle.innerText : '';
                            if (scene === keyword || title.includes(keyword)) {{
                                matchedRows.push(tr.cloneNode(true));
                            }}
                        }});
                    }});

                    tableBody.innerHTML = '';
                    matchedRows.forEach(row => tableBody.appendChild(row));

                    viewMenus.style.display = 'none';
                    viewTable.style.display = 'block';
                    titleEl.innerText = '全局搜索结果: ' + keyword;

                    currentRows = Array.from(tableBody.querySelectorAll('tr'));
                    const sets = {{ scene: new Set(), layer: new Set(), level: new Set() }};
                    currentRows.forEach(row => {{
                        if (row.dataset.scene && row.dataset.scene !== '无') sets.scene.add(row.dataset.scene);
                        if (row.dataset.layer && row.dataset.layer !== '无') sets.layer.add(row.dataset.layer);
                        if (row.dataset.level && row.dataset.level !== '无') sets.level.add(row.dataset.level);
                    }});

                    ['filter-scene', 'filter-layer', 'filter-level'].forEach((filterId, i) => {{
                        const key = ['scene', 'layer', 'level'][i];
                        const select = document.getElementById(filterId);
                        const oldVal = select.value;
                        select.innerHTML = '<option value="all">全部</option>';
                        Array.from(sets[key]).sort().forEach(v => {{
                            select.innerHTML += '<option value="' + v + '">' + v + '</option>';
                        }});
                        select.value = (oldVal === 'all') ? 'all' : (sets[key].has(oldVal) ? oldVal : 'all');
                    }});

                    applyFilters();
                }});
            }}
            document.addEventListener('DOMContentLoaded', initGlobalSearch);
        </script>
    </body>
    </html>
    """
    return final_html


# 核心处理接口：接收表单数据，返回完整单页应用 HTML
@app.post("/generate", response_class=HTMLResponse)
async def generate_dashboard(request: Request, jx3ids: str = Form(...)):
    try:
        raw_list = jx3ids.replace(",", " ").split()
        jx3id_list = [i.strip() for i in raw_list if i.strip()]
        if not jx3id_list:
            return "<h3>错误：请输入有效的 JX3ID</h3>"

        # ---- 读取登录态，查询备注名 ----
        session_user = request.cookies.get("session_user", "")
        remarks_map = {}
        if session_user:
            try:
                r = requests.get(
                    f"{TCB_JX3IDS_URL}/documents",
                    params={"where": json.dumps({"email": session_user}), "limit": 100},
                    headers=TCB_HEADERS, timeout=8
                )
                if r.status_code == 200:
                    body_r = r.json()
                    docs = body_r.get("list") or body_r.get("data") or []
                    if isinstance(docs, list):
                        for d in docs:
                            if d.get("email") == session_user:
                                remarks_map[d.get("jx3_uid", "")] = d.get("alias_name", "")
            except Exception:
                pass

        short_ids = [uid[-4:] if len(uid) >= 4 else uid for uid in jx3id_list]

        # 构建展示名映射：short_id → 备注名 或 原 short_id
        display_names = {}
        for i, full_id in enumerate(jx3id_list):
            sid = short_ids[i]
            if full_id in remarks_map:
                display_names[sid] = f"[{remarks_map[full_id]}]"
            else:
                display_names[sid] = sid

        # ---- 静默日志：记录查询请求（不影响主流程） ----
        try:
            requests.post(
                TCB_QUERY_LOGS_URL + "/documents",
                json={"data": [{"query_uids": jx3ids, "created_at": int(time.time())}]},
                headers=TCB_HEADERS,
                timeout=1.5,
            )
        except Exception:
            pass

        # ---------------- 阶段一：获取API数据 ----------------
        user_completed_map = {}
        headers = {"User-Agent": "Mozilla/5.0"}
        for index, full_id in enumerate(jx3id_list):
            sid = short_ids[index]
            api_url = f"https://next2.jx3box.com/api/next2/user-achievements?jx3id={full_id}"
            resp = requests.get(api_url, headers=headers, timeout=10)
            if resp.status_code != 200:
                return f"<h3>请求账号 {full_id} 失败，请检查 ID 是否正确或稍后再试。</h3>"

            raw_str = resp.json().get("data", {}).get("achievements", "")
            completed = set()
            if raw_str:
                for x in raw_str.split(","):
                    if x.strip().isdigit():
                        completed.add(int(x.strip()))
            user_completed_map[sid] = completed

        # ---- 调用可复用渲染函数 ----
        final_html = build_dashboard_html(short_ids, user_completed_map, display_names)

        # ---- 写入 CloudBase 缓存（仅存储 raw_data） ----
        if TCB_ENV_ID and TCB_API_KEY:
            try:
                raw_data = {
                    "jx3ids": jx3ids,
                    "user_completed_map": {k: list(v) for k, v in user_completed_map.items()},
                    "short_ids": short_ids,
                }
                payload = {"data": [{"raw_data": raw_data, "created_at": int(time.time())}]}
                print(f"DEBUG TCB URL: {TCB_COLLECTION_URL}/documents")
                resp = requests.post(f"{TCB_COLLECTION_URL}/documents", json=payload, headers=TCB_HEADERS, timeout=15)
                print(f"DEBUG TCB STATUS: {resp.status_code}")
                print(f"DEBUG TCB RESPONSE: {resp.text[:300]}")
                if resp.status_code in (200, 201):
                    body = resp.json()
                    doc_id = None

                    # 精准解析腾讯云返回的 insertedIds 数组
                    if "insertedIds" in body and isinstance(body["insertedIds"], list) and body["insertedIds"]:
                        doc_id = body["insertedIds"][0]
                    else:
                        # 兜底兼容
                        doc_id = body.get("id") or body.get("_id")

                    if doc_id:
                        print(f"DEBUG TCB DOC_ID: {doc_id}")
                        return RedirectResponse(f"/board/{doc_id}", status_code=303)
            except Exception as e:
                print(f"DEBUG TCB EXCEPTION: {repr(e)}")

        return final_html

    except Exception as e:
        return f"<h3>处理出错：{str(e)}</h3>"


@app.get("/board/{doc_id}", response_class=HTMLResponse)
async def view_board(request: Request, doc_id: str):
    """从 CloudBase 读取 raw_data，实时重建看板"""
    if not TCB_ENV_ID or not TCB_API_KEY:
        return HTMLResponse("<h3>数据库未配置（缺少 TCB_ENV_ID 或 TCB_API_KEY 环境变量）。</h3>", status_code=500)
    try:
        # 直接获取文档
        resp = requests.get(f"{TCB_COLLECTION_URL}/documents/{doc_id}", headers=TCB_HEADERS, timeout=10)
        print(f"DEBUG GET STATUS: {resp.status_code}")
        raw_data = None
        if resp.status_code == 200:
            body = resp.json()
            doc = body.get("data") or body.get("list") or body
            if isinstance(doc, dict) and "raw_data" in doc:
                raw_data = doc.get("raw_data")
            elif isinstance(doc, list) and len(doc) > 0:
                raw_data = doc[0].get("raw_data")

        # 回退：where 查询
        if raw_data is None:
            resp2 = requests.get(
                f"{TCB_COLLECTION_URL}/documents",
                params={"where": json.dumps({"_id": doc_id})},
                headers=TCB_HEADERS,
                timeout=10
            )
            if resp2.status_code == 200:
                body2 = resp2.json()
                docs = body2.get("list") or body2.get("data")
                if isinstance(docs, list) and len(docs) > 0:
                    raw_data = docs[0].get("raw_data")

        if not raw_data or not isinstance(raw_data, dict):
            return HTMLResponse("<h3>看板已过期、不存在或数据库连接失败。</h3>", status_code=404)

        # ==========================================
        # 补丁：在此处补充读取当前登录用户的备注数据
        # ==========================================
        session_user = request.cookies.get("session_user", "")
        remarks_map = {}
        if session_user:
            try:
                r = requests.get(
                    f"{TCB_JX3IDS_URL}/documents",
                    params={"where": json.dumps({"email": session_user}), "limit": 100},
                    headers=TCB_HEADERS, timeout=8
                )
                if r.status_code == 200:
                    body_r = r.json()
                    docs = body_r.get("list") or body_r.get("data") or []
                    if isinstance(docs, list):
                        for d in docs:
                            if d.get("email") == session_user:
                                remarks_map[d.get("jx3_uid", "")] = d.get("alias_name", "")
            except Exception:
                pass
        # ==========================================

        # 重建 set 并重新渲染，注意清洗腾讯云的 EJSON 格式
        short_ids_recv = raw_data.get("short_ids", [])
        raw_map = raw_data.get("user_completed_map", {})

        # 从 raw_data 恢复原始完整 UID 列表，用来匹配备注
        full_ids = [i.strip() for i in raw_data.get("jx3ids", "").replace(",", " ").split() if i.strip()]

        user_completed_map = {}
        for sid, val_list in raw_map.items():
            cleaned_set = set()
            for item in val_list:
                if isinstance(item, dict):
                    # 提取 {"$numberInt": "123"} 里面的真实数字
                    val = item.get("$numberInt") or item.get("$numberLong")
                    if val is not None:
                        cleaned_set.add(int(val))
                else:
                    # 兼容可能已经是整数或字符串的情况
                    cleaned_set.add(int(item))
            user_completed_map[sid] = cleaned_set

        # 构建 display_names 传给渲染函数
        display_names = {}
        for i, full_id in enumerate(full_ids):
            if i < len(short_ids_recv):
                sid = short_ids_recv[i]
                display_names[sid] = f"[{remarks_map[full_id]}]" if full_id in remarks_map else sid

        # 最后，带上 display_names 参数调用渲染器
        final_html = build_dashboard_html(short_ids_recv, user_completed_map, display_names)
        return HTMLResponse(final_html)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return HTMLResponse(f"<h3>页面渲染失败: {repr(e)}</h3>", status_code=500)


# ---- 管理页：查询日志 ----
@app.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs(request: Request):
    session_user = request.cookies.get("session_user", "")
    if session_user not in ADMIN_EMAILS:
        return HTMLResponse(
            "<h3 style='color:#ef4444;text-align:center;margin-top:80px;'>403 Forbidden — 权限不足，仅限管理员访问</h3>",
            status_code=403,
        )

    rows_html = ""
    try:
        resp = requests.get(
            f"{TCB_QUERY_LOGS_URL}/documents",
            params={"limit": 100},
            headers=TCB_HEADERS,
            timeout=8,
        )
        if resp.status_code == 200:
            body = resp.json()
            docs = body.get("list") or body.get("data") or []
            if isinstance(docs, list) and docs:
                # 修复核心：清洗腾讯云的 EJSON 时间戳
                def get_ts(d):
                    val = d.get("created_at", 0)
                    if isinstance(val, dict):
                        return int(val.get("$numberInt") or val.get("$numberLong") or 0)
                    return int(val) if val else 0

                # Python 侧时间倒序排列
                docs.sort(key=get_ts, reverse=True)
                for d in docs:
                    ts = get_ts(d)
                    time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"
                    uids = d.get("query_uids", "-")
                    rows_html += f"<tr><td style='white-space:nowrap;'>{time_str}</td><td>{uids}</td></tr>"
        else:
            rows_html = f"<tr><td colspan='2' style='color:#ef4444;'>API 请求失败: {resp.status_code}</td></tr>"
    except Exception as e:
        rows_html = f"<tr><td colspan='2' style='color:#ef4444;'>读取日志失败: {str(e)}</td></tr>"

    if not rows_html:
        rows_html = "<tr><td colspan='2' style='color:#94a3b8;'>暂无查询记录</td></tr>"

    return f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>查询日志 · 管理面板</title>
        <style>
            body {{ font-family: 'Segoe UI', -apple-system, sans-serif; background: #f8fafc; padding: 40px; margin: 0; }}
            .container {{ max-width: 800px; margin: 0 auto; }}
            h2 {{ color: #1e293b; margin-bottom: 20px; display: flex; align-items: center; justify-content: space-between; }}
            .back-btn {{ font-size: 14px; color: #3b82f6; text-decoration: none; padding: 6px 12px; background: #e0f2fe; border-radius: 6px; font-weight: bold; }}
            table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }}
            th {{ background: #3b82f6; color: white; padding: 14px 20px; text-align: left; font-size: 15px; font-weight: 600; }}
            td {{ padding: 12px 20px; border-bottom: 1px solid #f1f5f9; font-size: 14px; color: #475569; }}
            tr:last-child td {{ border-bottom: none; }}
            tr:hover {{ background: #f8fafc; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>📋 后台查询日志 <a href="/" class="back-btn">← 返回首页</a></h2>
            <table>
                <thead><tr><th width="180px">查询时间</th><th>查询的 UID</th></tr></thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
    </body>
    </html>
    """
