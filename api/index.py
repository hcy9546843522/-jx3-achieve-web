from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
import requests
import json
import os
import hashlib
import gzip
import time
import httpx
import asyncio
import sqlite3
import uuid

app = FastAPI()

# 获取当前文件的绝对路径，确保能在 Vercel 环境中找到根目录的 json
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_JSON_FILE = os.path.join(BASE_DIR, "jx3_all_backup.json")

# 本地 SQLite 数据库路径
DB_PATH = os.path.join(BASE_DIR, "jx3_local.db")

COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#14b8a6", "#f43f5e"]


# ==========================================
# 数据库初始化 (sqlite3 本地化)
# ==========================================
def init_db():
    print("DEBUG: 正在初始化本地 SQLite 数据库...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_jx3ids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            jx3_uid TEXT NOT NULL,
            alias_name TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS html_cache (
            id TEXT PRIMARY KEY,
            raw_data TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS query_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_uids TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
    ''')
    conn.commit()
    conn.close()
    print(f"DEBUG: 数据库初始化完成，路径: {DB_PATH}")


@app.on_event("startup")
async def startup_db():
    init_db()


# ==========================================
# 新增模块：异步并发拉取地图事件
# ==========================================
async def fetch_all_events():
    urls = {
        "楚天社": "https://cms.jx3box.com/api/cms/game/celebrity?type=0",
        "云从社": "https://cms.jx3box.com/api/cms/game/celebrity?type=1",
        "披风会": "https://cms.jx3box.com/api/cms/game/celebrity?type=2",
        "穹野卫": "https://cms.jx3box.com/api/cms/game/celebrity?type=3"
    }
    events_data = {}

    async with httpx.AsyncClient(timeout=5.0) as client:
        tasks = [client.get(url) for url in urls.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for name, res in zip(urls.keys(), results):
            if not isinstance(res, Exception) and res.status_code == 200:
                events_data[name] = res.json().get("data", [])
            else:
                events_data[name] = []
    return events_data


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
            .copy-btn {{ background: #e0f2fe; color: #3b82f6; border-radius: 4px; padding: 4px 8px; font-size: 12px; cursor: pointer; font-weight: bold; }}
            .copy-btn:hover {{ background: #bae6fd; }}

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
            <div style="text-align:center; padding: 12px 0;">
                <a href="https://docs.qq.com/doc/DQXV1WVhsYmtMVVRY" target="_blank" style="color:#3b82f6; font-size:14px; text-decoration:none; font-weight:500; border-bottom:1px dashed #3b82f6;">📖 使用说明文档</a>
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
                        <div>
                            <span class="copy-btn" onclick="copyUid('${{x.jx3_uid}}')">复制</span>
                            <span class="del-btn" onclick="delUid('${{x.jx3_uid}}')">删除</span>
                        </div>
                    </div>
                `).join('');
            }}

            async function delUid(uid) {{
                await fetch('/api/del_uid?jx3_uid='+uid, {{ method:'DELETE' }});
                loadUids();
            }}

            function copyUid(uid) {{
                if (navigator.clipboard && window.isSecureContext) {{
                    navigator.clipboard.writeText(uid).then(() => showMsg('已复制 UID: ' + uid, '#10b981'));
                }} else {{
                    const textArea = document.createElement("textarea");
                    textArea.value = uid;
                    document.body.appendChild(textArea);
                    textArea.select();
                    try {{
                        document.execCommand('copy');
                        showMsg('已复制 UID: ' + uid, '#10b981');
                    }} catch (err) {{
                        showMsg('复制失败，请手动选择复制', '#ef4444');
                    }}
                    document.body.removeChild(textArea);
                }}
            }}

            {init_script}
        </script>
    </body>
    </html>
    """


# ---- 账户体系 API (完全重构为 SQLite) ----
@app.post("/api/register")
async def api_register(request: Request):
    try:
        body = await request.json()
        email = body.get("email", "").strip()
        password = body.get("password", "").strip()
        if not email or not password:
            return {"detail": "邮箱和密码不能为空"}

        pw_hash = hashlib.sha256(password.encode()).hexdigest()

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            # 检查是否已存在
            cur = conn.execute("SELECT id FROM users WHERE email=?", (email,))
            if cur.fetchone():
                return {"detail": "邮箱已注册"}

            # 插入新用户
            conn.execute("INSERT INTO users (email, password) VALUES (?, ?)", (email, pw_hash))
            conn.commit()

            response = JSONResponse({"detail": "注册成功"})
            response.set_cookie("session_user", email, path="/", max_age=86400 * 30)
            return response
        finally:
            conn.close()
    except Exception as e:
        print(f"DEBUG: 注册接口报错 {e}")
        return {"detail": f"服务器错误: {str(e)}"}


@app.post("/api/login")
async def api_login(request: Request):
    try:
        body = await request.json()
        email = body.get("email", "").strip()
        password = body.get("password", "").strip()
        if not email or not password:
            return {"detail": "邮箱和密码不能为空"}

        pw_hash = hashlib.sha256(password.encode()).hexdigest()

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute("SELECT password FROM users WHERE email=?", (email,))
            row = cur.fetchone()
            if not row:
                return {"detail": "邮箱未注册"}
            if row["password"] != pw_hash:
                return {"detail": "密码错误"}

            response = JSONResponse({"detail": "登录成功"})
            response.set_cookie("session_user", email, path="/", max_age=86400 * 30)
            return response
        finally:
            conn.close()
    except Exception as e:
        print(f"DEBUG: 登录接口报错 {e}")
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

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            # 查询是否存在该 UID
            cur = conn.execute("SELECT id FROM user_jx3ids WHERE email=? AND jx3_uid=?", (session_user, jx3_uid))
            row = cur.fetchone()
            if row:
                conn.execute("UPDATE user_jx3ids SET alias_name=? WHERE id=?", (alias_name, row["id"]))
            else:
                conn.execute("INSERT INTO user_jx3ids (email, jx3_uid, alias_name) VALUES (?, ?, ?)",
                             (session_user, jx3_uid, alias_name))
            conn.commit()
            return {"detail": "保存成功"}
        finally:
            conn.close()
    except Exception as e:
        print(f"DEBUG: 保存UID接口报错 {e}")
        return {"detail": f"服务器错误: {str(e)}"}


@app.get("/api/my_uids")
async def api_my_uids(request: Request):
    session_user = request.cookies.get("session_user", "")
    if not session_user:
        return {"uids": []}
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute("SELECT jx3_uid, alias_name FROM user_jx3ids WHERE email=?", (session_user,))
            rows = cur.fetchall()
            result = [{"jx3_uid": r["jx3_uid"], "alias_name": r["alias_name"]} for r in rows]
            return {"uids": result}
        finally:
            conn.close()
    except Exception as e:
        print(f"DEBUG: 查询UID接口报错 {e}")
        return {"uids": []}


@app.delete("/api/del_uid")
async def api_del_uid(request: Request, jx3_uid: str = ""):
    session_user = request.cookies.get("session_user", "")
    if not session_user:
        return {"detail": "请先登录"}
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("DELETE FROM user_jx3ids WHERE email=? AND jx3_uid=?", (session_user, jx3_uid))
            conn.commit()
            return {"detail": "已删除"}
        finally:
            conn.close()
    except Exception as e:
        print(f"DEBUG: 删除UID接口报错 {e}")
        return {"detail": f"删除失败: {str(e)}"}


# ---- 可复用渲染函数：根据完成数据重新生成完整 HTML ----
# (此部分完全保留你原本的 UI 和 DOM 组装逻辑，不作任何改动)
def build_dashboard_html(short_ids, user_completed_map, display_names=None, events_data=None):
    """阶段二~四：读取本地 JSON → 统计进度 → 组装前端 HTML"""
    import hashlib as _hashlib
    dn = display_names or {}
    import json as _json
    ev_data_json = _json.dumps(events_data or {}, ensure_ascii=False)

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

            # --- 重新拼装每行的数据属性 ---
            subs_list = ach.get("SubAchievementList") or ach.get("SubAchievements") or []
            sub_names = []  # 用于全局搜索的全部子成就
            uncompleted_sub_names = []  # 仅存未完成的子成就，用于雷达精准高亮

            if isinstance(subs_list, list):
                for sub in subs_list:
                    sub_id = sub.get("ID")
                    s_name = sub.get("ShortDesc") or sub.get("Name") or sub.get("name") or str(sub_id)
                    if s_name:
                        sub_names.append(s_name)

                        # 判断这个具体的子成就是否有任何账号未完成
                        is_all_done = True
                        for sid in short_ids:
                            if not (sub_id and int(sub_id) in user_completed_map[sid]):
                                is_all_done = False
                                break
                        # 只有真的没人做过这个具体小事件，才把它塞进未完成池子
                        if not is_all_done:
                            uncompleted_sub_names.append(s_name)

            sub_names_str = ",".join(sub_names)
            uncompleted_subs_str = ",".join(uncompleted_sub_names)

            # 独立新增 data-uncompleted-subs 属性专供雷达读取
            tr_data_attrs = f"data-scene='{scene}' data-layer='{layer}' data-level='{level_val}' data-subs='{sub_names_str}' data-uncompleted-subs='{uncompleted_subs_str}' "
            td_status_html = ""

            # 1. 状态列恢复纯粹与紧凑
            for sid in short_ids:
                is_completed = ach_id in user_completed_map[sid]
                status_val = "yes" if is_completed else "no"
                tr_data_attrs += f"data-status-{sid}='{status_val}' "
                if is_completed:
                    stats[m1]["users"][sid] += 1
                    stats[m1]["subs"][m2]["users"][sid] += 1
                    td_status_html += f"<td><button class='badge badge-status badge-status-yes' data-uid='{sid}' data-val='yes'>✅ 完</button></td>"
                else:
                    td_status_html += f"<td><button class='badge badge-status badge-status-no' data-uid='{sid}' data-val='no'>❌ 未</button></td>"

            # 2. 精细化排版：在后端对子成就进行多账号合并及优化过滤（防止大量子成就撑爆表格）
            subs_html = ""
            if subs_list and isinstance(subs_list, list):
                subs_html += "<div class='sub-items-box'>"
                parsed_subs = []
                for sub in subs_list:
                    sub_id = sub.get("ID")
                    sub_name = sub.get("ShortDesc") or sub.get("Name") or str(sub_id)

                    sub_status = {}
                    is_all_done = True
                    for sid in short_ids:
                        done = sub_id and int(sub_id) in user_completed_map[sid]
                        sub_status[sid] = done
                        if not done: is_all_done = False

                    parsed_subs.append({
                        "id": sub_id, "name": sub_name, "status": sub_status, "is_all_done": is_all_done
                    })

                # 核心约束：优先把未在所有账号全部完成的子成就排在前面
                parsed_subs.sort(key=lambda x: x["is_all_done"])

                # 核心约束：最多只展示前6个，防止排版错乱
                max_show = 6
                show_subs = parsed_subs[:max_show]
                has_more = len(parsed_subs) > max_show

                for ps in show_subs:
                    dots = ""
                    for sid in short_ids:
                        color = "#10b981" if ps["status"][sid] else "#ef4444"
                        completed_text = "已完成" if ps["status"][sid] else "未完成"
                        dots += f"<span class='sub-status-dot' style='background:{color}' title='{dn.get(sid, sid)}: {completed_text}'></span>"

                    style_class = "sub-item-all-done" if ps["is_all_done"] else "sub-item-not-done"
                    subs_html += f"<div class='sub-item-row {style_class}' data-id='{ps['id']}'>" \
                                 f"<span class='sub-item-name'>• {ps['name']}</span>" \
                                 f"<span class='sub-item-dots'>{dots}</span>" \
                                 f"</div>"
                if has_more:
                    remain_not_done = sum(1 for x in parsed_subs[max_show:] if not x["is_all_done"])
                    if remain_not_done > 0:
                        subs_html += f"<div class='sub-item-more'>还有 {remain_not_done} 项未完成...</div>"
                    else:
                        subs_html += f"<div class='sub-item-more'>其余均已完成</div>"
                subs_html += "</div>"

            scene_html = f"<button class='badge badge-scene' data-val='{scene}'>{scene}</button>" if scene != "无" else "-"
            layer_html = f"<button class='badge badge-layer' data-val='{layer}'>{layer}</button>" if layer != "无" else "-"
            level_html = f"<button class='badge badge-level' data-val='{level_val}'>{level_val}</button>" if str(
                level_val) != "无" else "-"
            guide_html = f"""<details><summary>💡 展开攻略详情</summary><div class="guide-content">{content}</div></details>""" if content.strip() else ""

            # 将原本在状态列下面的子成就，重构塞进"成就信息"列（即包含名和ID的一列）
            html_templates[template_id] += f"""
            <tr {tr_data_attrs}>
                {td_status_html}
                <td>{scene_html}</td>
                <td>{layer_html}</td>
                <td>{level_html}</td>
                <td>
                    <div class="ach-title">{ach.get("Name", "未知")}</div>
                    <div class="ach-id">ID: {ach_id}</div>
                    {subs_html}
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
            /* 布局核心：左看板，右雷达 */
            .main-layout {{ display: flex; gap: 20px; align-items: flex-start; }}
            .left-board {{ flex: 1; min-width: 0; }}
            .right-radar {{ width: 280px; flex-shrink: 0; background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); position: sticky; top: 20px; max-height: calc(100vh - 40px); overflow-y: auto; }}
            .radar-title {{ font-size: 16px; font-weight: bold; color: #1e293b; margin-top: 0; border-bottom: 2px dashed var(--border); padding-bottom: 10px; margin-bottom: 10px; display: flex; align-items: center; gap: 6px; }}
            /* 雷达内部事件卡片 */
            .radar-group {{ margin-bottom: 15px; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }}
            /* 子成就精细化排版框 */
            .sub-items-box {{ margin-top: 8px; padding: 6px 10px; background: #f8fafc; border-radius: 6px; border: 1px dashed #e2e8f0; max-width: 100%; }}
            .sub-item-row {{ display: flex; align-items: center; justify-content: space-between; font-size: 12px; padding: 2px 0; border-bottom: 1px dashed #f1f5f9; }}
            .sub-item-row:last-child {{ border-bottom: none; }}
            .sub-item-name {{ color: #475569; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 140px; }}
            .sub-item-all-done .sub-item-name {{ color: #94a3b8; text-decoration: line-through; }}
            /* 多账号状态小圆点并排 */
            .sub-item-dots {{ display: flex; gap: 4px; align-items: center; }}
            .sub-status-dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; cursor: help; }}
            .sub-item-more {{ font-size: 11px; color: #f59e0b; margin-top: 4px; font-weight: bold; text-align: center; }}

            /* 雷达折叠样式 */
            .radar-group-title {{ background: #f1f5f9; padding: 8px 12px; font-size: 13px; font-weight: bold; color: #3b82f6; cursor: pointer; display: flex; justify-content: space-between; align-items: center; user-select: none; }}
            .radar-group-title::after {{ content: '▼'; font-size: 10px; color: #94a3b8; transition: transform 0.2s; }}
            .radar-group.expanded .radar-group-title::after {{ transform: rotate(180deg); }}
            .radar-items-container {{ display: none; }}
            .radar-group.expanded .radar-items-container {{ display: block; }}
            @keyframes blink {{ 50% {{ opacity: 0.4; }} }}
            .radar-item {{ padding: 8px 12px; border-top: 1px solid #f1f5f9; cursor: pointer; transition: background 0.2s; }}
            .radar-item:hover {{ background: #eff6ff; }}
            .radar-item-stage {{ font-size: 13px; font-weight: bold; color: #1e293b; }}
            .radar-item-time {{ font-size: 11px; color: #64748b; margin-top: 4px; }}
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
        <div class="main-layout">
            <div class="left-board">
                <div class="global-search-bar"><input type="text" id="global-search" class="global-search-input" placeholder="🔍 全局搜索成就名、事件名或场景（如'战宝军械库'）..."></div>
                <div id="view-menus">{dashboard_html}</div>
                <div id="view-table">
                    <div class="table-header-bar">
                        <button class="btn-back" onclick="closeTable()">🔙 返回看板</button>
                        <h2 id="current-table-title" style="margin:0; color:#334155;">-</h2>
                        <div style="width: 100px;"></div>
                    </div>
                    <div class="filter-bar">
                        {status_filters_html}
                        <div class="filter-group"><label>场景:</label><div class="select-wrapper"><select id="filter-scene" class="filter-select"><option value="all">全部</option></select><button class="clear-single" data-target="filter-scene">✖</button></div></div>
                        <div class="filter-group"><label>层级:</label><div class="select-wrapper"><select id="filter-layer" class="filter-select"><option value="all">全部</option></select><button class="clear-single" data-target="filter-layer">✖</button></div></div>
                        <div class="filter-group"><label>难度:</label><div class="select-wrapper"><select id="filter-level" class="filter-select"><option value="all">全部</option></select><button class="clear-single" data-target="filter-level">✖</button></div></div>
                        <div class="filter-group" style="margin-left: auto;"><input type="text" id="filter-search" class="filter-search-input" placeholder="🔍 搜索成就名或ID..."></div>
                    </div>
                    <table>
                        <thead>
                            <tr>
                                {status_th_html}
                                <th width="100px">场景</th><th width="100px">层级</th><th width="60px">难度</th><th width="200px">成就信息</th><th>达成条件 & 攻略</th><th width="60px">资历</th>
                            </tr>
                        </thead>
                        <tbody id="table-body"></tbody>
                    </table>
                </div>
            </div>

            <div class="right-radar">
                <h3 class="radar-title">📡 实时事件雷达</h3>
                <div id="radar-content"><div style="color:#94a3b8; font-size:13px; text-align:center;">正在解析数据...</div></div>
            </div>
        </div>
        <div id="templates-pool" style="display:none;">{templates_html}</div>
        <script>
            const userIds = {json.dumps(short_ids)};
            const mapEventsData = {ev_data_json};
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
            // Badge 点击快捷筛选
            document.getElementById('table-body').addEventListener('click', function(e) {{
                const badge = e.target.closest('.badge');
                if (!badge) return;
                if (badge.classList.contains('badge-scene')) {{
                    document.getElementById('filter-scene').value = badge.dataset.val;
                }} else if (badge.classList.contains('badge-layer')) {{
                    document.getElementById('filter-layer').value = badge.dataset.val;
                }} else if (badge.classList.contains('badge-level')) {{
                    document.getElementById('filter-level').value = badge.dataset.val;
                }} else if (badge.classList.contains('badge-status')) {{
                    const uid = badge.dataset.uid;
                    document.getElementById('filter-status-' + uid).value = badge.dataset.val;
                }} else {{
                    return;
                }}
                applyFilters();
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
                            const subs = tr.dataset.subs || ''; // 新增：读取隐藏的子成就数据
                            // 新增：只要场景、标题或任何子成就名字包含关键字，就判定为匹配
                            if (scene === keyword || title.includes(keyword) || subs.includes(keyword)) {{
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
            function renderRadar() {{
                const container = document.getElementById('radar-content');
                if (!mapEventsData || Object.keys(mapEventsData).length === 0) {{
                    container.innerHTML = '<div style="color:#ef4444; font-size:13px; text-align:center;">暂无地图事件数据</div>';
                    return;
                }}

                // 【核心修复 2】：前端只抓取后端精确算好的 uncompletedSubs 属性，不再被父成就误导
                const uncompletedSubsSet = new Set();
                document.querySelectorAll('#templates-pool template').forEach(tpl => {{
                    tpl.content.querySelectorAll('tr').forEach(tr => {{
                        if (tr.dataset.uncompletedSubs) {{
                            tr.dataset.uncompletedSubs.split(',').forEach(s => {{
                                if (s.trim()) uncompletedSubsSet.add(s.trim());
                            }});
                        }}
                    }});
                }});

                const expandedStates = {{}};
                document.querySelectorAll('.radar-group').forEach(el => {{
                    expandedStates[el.id] = el.classList.contains('expanded');
                }});

                const now = new Date();
                const bjtTime = new Date(now.getTime() + (now.getTimezoneOffset() * 60000) + (8 * 3600000));
                const currentH = bjtTime.getHours();
                const currentM = bjtTime.getMinutes();
                const currentTotalM = currentH * 60 + currentM;

                let html = '';
                const targetOrder = ["穹野卫", "披风会", "云从社", "楚天社"];
                const sortedFactions = Object.keys(mapEventsData).sort((a, b) => {{
                    let idxA = targetOrder.indexOf(a), idxB = targetOrder.indexOf(b);
                    return (idxA === -1 ? 999 : idxA) - (idxB === -1 ? 999 : idxB);
                }});
                for (const faction of sortedFactions) {{
                    const events = mapEventsData[faction];
                    if (!events || !Array.isArray(events) || events.length === 0) continue;

                    let cycleLength = 2; 
                    if (faction.includes("穹野卫") || faction.includes("披风会") || faction.includes("伊丽川")) {{
                        cycleLength = 3;
                    }}

                    let groupHighlight = false; 
                    const processedEvents = [];

                    events.forEach(ev => {{
                        let evHour = parseInt(ev.hour);

                        // 【核心修复】：银霜口(云从社)特殊判定，通过 key 强制绑定奇偶轨道
                        // y0 强制为偶数轨(0), y1 强制为奇数轨(1)
                        if (faction.includes("云从社") || (ev.map && (ev.map.includes("银霜") || ev.map.includes("银双")))) {{
                            if (ev.key === "y0") evHour = 0;
                            if (ev.key === "y1") evHour = 1;
                            cycleLength = 2; // 确保云从社严格遵守2小时双轨
                        }}

                        // 【核心修复】：楚天社特殊判定，无视API残缺数据，通过地图严格绑定奇偶轨道
                        if (faction.includes("楚天社")) {{
                            if (ev.map && (ev.map.includes("烂柯山") || ev.map.includes("晟江"))) {{
                                evHour = 0; // 偶数点轨道
                            }}
                            if (ev.map && (ev.map.includes("百溪") || ev.map.includes("楚州"))) {{
                                evHour = 1; // 奇数点轨道
                            }}
                            cycleLength = 2; // 确保双轨循环
                        }}

                        if (isNaN(evHour)) return; 

                        let targetH = currentH;
                        let loopGuard = 0;
                        while ((targetH % cycleLength) != evHour && loopGuard < 24) {{
                            targetH++;
                            loopGuard++;
                        }}

                        let targetTotalM = targetH * 60 + parseInt(ev.time || 0);
                        let diff = targetTotalM - currentTotalM;

                        if (diff < -15) {{
                            targetH += cycleLength;
                            targetTotalM += cycleLength * 60;
                            diff = targetTotalM - currentTotalM;
                        }}

                        let displayH = targetH % 24;

                        let status = 0; 
                        if (diff <= 0 && diff >= -5) status = 1; 
                        else if (diff < -5) status = -1; 

                        const isAssociatedWithNotDone = uncompletedSubsSet.has(ev.stage);

                        // 【核心修复 1】：将"有需求"的判定加上严格的时间锁 (30分钟以内才算)
                        const isNeededAndUpcoming = isAssociatedWithNotDone && diff <= 30 && status !== -1;

                        if (isNeededAndUpcoming) {{
                            groupHighlight = true;
                        }}

                        const timeStr = String(displayH).padStart(2, '0') + ':' + String(parseInt(ev.time || 0)).padStart(2, '0');
                        processedEvents.push({{ ...ev, timeStr, diff, status, isAssociatedWithNotDone, isNeededAndUpcoming }});
                    }});

                    processedEvents.sort((a, b) => {{
                        if (a.status === -1 && b.status !== -1) return 1;
                        if (a.status !== -1 && b.status === -1) return -1;

                        // 【排序修复】：只有同时满足"未完成"且"30分钟以内"，才有资格置顶
                        if (a.isNeededAndUpcoming && !b.isNeededAndUpcoming) return -1;
                        if (!a.isNeededAndUpcoming && b.isNeededAndUpcoming) return 1;

                        return a.diff - b.diff;
                    }});

                    const groupTitleStyle = groupHighlight ? 'background: #fefce8; color: #d97706; animation: blink 2s infinite;' : '';
                    const expandedClass = expandedStates[`radar-g-${{faction}}`] ? 'expanded' : '';

                    html += `<div class="radar-group ${{expandedClass}}" id="radar-g-${{faction}}">
                                <div class="radar-group-title" style="${{groupTitleStyle}}" onclick="document.getElementById('radar-g-${{faction}}').classList.toggle('expanded')">
                                    ${{faction}} ${{groupHighlight ? '🔥 有需求' : ''}}
                                </div>
                                <div class="radar-items-container">`;

                    processedEvents.forEach(ev => {{
                        let colorStyle = '';
                        let statusTag = '';

                        const isUpcomingWithin10 = (ev.status === 0 && ev.diff > 0 && ev.diff <= 10);

                        if (ev.status === 1) {{
                            if (ev.isNeededAndUpcoming) {{
                                colorStyle = 'background: #fefce8; border-left: 3px solid #f59e0b; font-weight: bold;';
                                statusTag = '<span style="font-size:10px; color:#d97706; margin-left:6px; animation: blink 1.5s infinite;">🔥 正在进行</span>';
                            }} else {{
                                colorStyle = 'background: #f8fafc; border-left: 3px solid #cbd5e1;';
                                statusTag = '<span style="font-size:10px; color:#64748b; margin-left:6px;">🟢 正在进行(成就已拿)</span>';
                            }}
                        }} else if (isUpcomingWithin10) {{
                            // 10分钟内，强制套用无需求的进行中格式（灰底、灰字、绿点）
                            colorStyle = 'background: #f8fafc; border-left: 3px solid #cbd5e1;';
                            if (ev.isNeededAndUpcoming) {{
                                statusTag = `<span style="font-size:10px; color:#64748b; margin-left:6px; font-weight:bold;">🟢 ${{ev.diff}}分钟后(有需求)</span>`;
                            }} else {{
                                statusTag = `<span style="font-size:10px; color:#64748b; margin-left:6px;">🟢 ${{ev.diff}}分钟后</span>`;
                            }}
                        }} else if (ev.status === -1) {{
                            colorStyle = 'opacity: 0.5; filter: grayscale(1); background: #f1f5f9;';
                            statusTag = '<span style="font-size:10px; color:#94a3b8; margin-left:6px;">🏁 已结束</span>';
                        }} else {{
                            if (ev.isNeededAndUpcoming) {{
                                colorStyle = 'background: #f0f9ff; border-left: 3px solid #0284c7; font-weight: bold;';
                                statusTag = `<span style="font-size:10px; color:#0284c7; margin-left:6px;">⏳ ${{ev.diff}}分钟后</span>`;
                            }} else {{
                                colorStyle = 'opacity: 0.6; filter: grayscale(1);';
                                statusTag = `<span style="font-size:10px; color:#94a3b8; margin-left:6px;">⏳ ${{ev.diff}}分钟后</span>`;
                            }}
                        }}

                        const mapNameHtml = ev.map ? `<span style="color:#0284c7; font-weight:bold; margin-right:4px;">[${{ev.map}}]</span>` : '';

                        html += `<div class="radar-item" style="${{colorStyle}}" onclick="triggerRadarSearch('${{ev.stage}}')" title="${{ev.desc}}">
                                    <div class="radar-item-stage">${{mapNameHtml}}🎯 ${{ev.stage}} <span style="font-size:11px;color:#94a3b8;font-weight:normal;">(${{ev.site || '未知'}})</span></div>
                                    <div class="radar-item-time">⏱ ${{ev.timeStr}} ${{statusTag}}</div>
                                 </div>`;
                    }});
                    html += `</div></div>`;
                }}
                container.innerHTML = html;
            }}

            function triggerRadarSearch(keyword) {{
                const input = document.getElementById('global-search');
                if (input) {{
                    input.value = keyword;
                    // 派发 input 事件，触发原有的 initGlobalSearch 逻辑
                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    // 滚动到顶部，方便看搜索结果
                    window.scrollTo({{ top: 0, behavior: 'smooth' }});
                }}
            }}

            document.addEventListener('DOMContentLoaded', () => {{
                initGlobalSearch();
                renderRadar();
                // 每隔 30 秒自动刷新一次雷达倒计时
                setInterval(renderRadar, 30000);
            }});
        </script>
    </body>
    </html>
    """
    return final_html


# ==========================================
# 核心处理接口：接收表单数据，返回完整单页应用 HTML
# (完全重构为 SQLite)
# ==========================================
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
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                try:
                    cur = conn.execute("SELECT jx3_uid, alias_name FROM user_jx3ids WHERE email=?", (session_user,))
                    for r in cur.fetchall():
                        remarks_map[r["jx3_uid"]] = r["alias_name"]
                finally:
                    conn.close()
            except Exception as e:
                print(f"DEBUG: 获取备注报错 {e}")

        short_ids = [uid[-4:] if len(uid) >= 4 else uid for uid in jx3id_list]

        # 构建展示名映射：short_id → 备注名 或 原 short_id
        display_names = {}
        for i, full_id in enumerate(jx3id_list):
            sid = short_ids[i]
            if full_id in remarks_map:
                display_names[sid] = f"[{remarks_map[full_id]}]"
            else:
                display_names[sid] = sid

        # ---- 静默日志：记录查询请求（存入 sqlite） ----
        try:
            conn = sqlite3.connect(DB_PATH)
            try:
                conn.execute(
                    "INSERT INTO query_logs (query_uids, created_at) VALUES (?, ?)",
                    (jx3ids, int(time.time()))
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"DEBUG: 写入查询日志失败 {e}")

        # ---------------- 阶段一：获取API数据 ----------------
        user_completed_map = {}
        headers = {"User-Agent": "Mozilla/5.0"}
        for index, full_id in enumerate(jx3id_list):
            sid = short_ids[index]
            api_url = f"https://next2.jx3box.com/api/next2/user-achievements?jx3id={full_id}"
            try:
                print(f"DEBUG: 正在拉取魔盒API，UID={full_id}")
                resp = requests.get(api_url, headers=headers, timeout=30)
            except requests.exceptions.Timeout:
                return f"<h3>请求账号 {full_id} 超时（服务器位于境外，跨网访问剑三API较慢，请稍后重试）。</h3>"
            except requests.exceptions.ConnectionError:
                return f"<h3>无法连接剑三数据服务器，请稍后重试。账号：{full_id}</h3>"
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
        events_data = await fetch_all_events()
        final_html = build_dashboard_html(short_ids, user_completed_map, display_names, events_data)

        # ---- 写入 SQLite 缓存（仅存储 raw_data） ----
        try:
            doc_id = uuid.uuid4().hex
            raw_data = {
                "jx3ids": jx3ids,
                "user_completed_map": {k: list(v) for k, v in user_completed_map.items()},
                "short_ids": short_ids,
            }
            raw_data_str = json.dumps(raw_data, ensure_ascii=False)

            conn = sqlite3.connect(DB_PATH)
            try:
                conn.execute(
                    "INSERT INTO html_cache (id, raw_data, created_at) VALUES (?, ?, ?)",
                    (doc_id, raw_data_str, int(time.time()))
                )
                conn.commit()
            finally:
                conn.close()

            print(f"DEBUG: 缓存已存入本地 DB，doc_id: {doc_id}")
            return RedirectResponse(f"/board/{doc_id}", status_code=303)
        except Exception as e:
            print(f"DEBUG DB EXCEPTION: {repr(e)}")

        return final_html

    except Exception as e:
        print(f"DEBUG: Generate 阶段报错 {e}")
        return f"<h3>处理出错：{str(e)}</h3>"


@app.get("/board/{doc_id}", response_class=HTMLResponse)
async def view_board(request: Request, doc_id: str):
    """从本地 SQLite 读取 raw_data，实时重建看板"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        raw_data = None
        try:
            cur = conn.execute("SELECT raw_data FROM html_cache WHERE id=?", (doc_id,))
            row = cur.fetchone()
            if row:
                raw_data = json.loads(row["raw_data"])
        finally:
            conn.close()

        if not raw_data or not isinstance(raw_data, dict):
            return HTMLResponse("<h3>看板已过期、不存在或数据库连接失败。</h3>", status_code=404)

        # ==========================================
        # 补丁：在此处补充读取当前登录用户的备注数据
        # ==========================================
        session_user = request.cookies.get("session_user", "")
        remarks_map = {}
        if session_user:
            try:
                conn2 = sqlite3.connect(DB_PATH)
                conn2.row_factory = sqlite3.Row
                try:
                    cur = conn2.execute("SELECT jx3_uid, alias_name FROM user_jx3ids WHERE email=?", (session_user,))
                    for r in cur.fetchall():
                        remarks_map[r["jx3_uid"]] = r["alias_name"]
                finally:
                    conn2.close()
            except Exception as e:
                print(f"DEBUG: Board读取备注报错 {e}")
        # ==========================================

        # 重建 set 并重新渲染，注意清洗腾讯云可能残留的 EJSON 格式（兼容旧数据导入）
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

        # 🔥 修复点：在跳转后的看板页面，重新拉取一次实时的地图事件
        real_events_data = await fetch_all_events()

        # 最后，带上 display_names 和实时的 events_data 参数调用渲染器
        final_html = build_dashboard_html(short_ids_recv, user_completed_map, display_names,
                                          events_data=real_events_data)
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
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute("SELECT query_uids, created_at FROM query_logs ORDER BY created_at DESC LIMIT 100")
            rows = cur.fetchall()

            if rows:
                for d in rows:
                    ts = d["created_at"]
                    time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"
                    uids = d["query_uids"] or "-"
                    rows_html += f"<tr><td style='white-space:nowrap;'>{time_str}</td><td>{uids}</td></tr>"
        finally:
            conn.close()
    except Exception as e:
        print(f"DEBUG: 读取日志报错 {e}")
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