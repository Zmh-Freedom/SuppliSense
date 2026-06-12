"""
供应商风险分析系统 - Streamlit 前端
启动：streamlit run frontend/app.py --server.port 8501
"""

import uuid

import httpx
import streamlit as st

API = "http://localhost:8000"

st.set_page_config(page_title="供应商风险分析", page_icon="🤖", layout="wide", initial_sidebar_state="expanded")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []
if "active_tab" not in st.session_state:
    st.session_state.active_tab = 0

st.markdown("""
<style>
    /* ---- global ---- */
    #MainMenu, footer, .stDeployButton, [data-testid="stSidebarCollapseButton"] { display: none; }
    body, .stApp { background: #fafafa; color: #1a1a1a; }

    /* ---- sidebar ---- */
    [data-testid="stSidebar"] { background: #f5f5f0; border-right: 1px solid #e8e8e3; }
    [data-testid="stSidebar"] .stMarkdown h3 { font-size: 1rem; font-weight: 600; color: #333; }

    /* ---- chat ---- */
    [data-testid="stChatMessage"] {
        border-radius: 1rem !important; padding: 0.6rem 1rem !important;
        margin: 0.3rem 0 !important; max-width: 75% !important; border: none !important;
    }
    [data-testid="stChatMessage"][data-testid*="user"] {
        background: #f0f0eb !important; margin-left: auto !important;
    }
    [data-testid="stChatMessage"][data-testid*="assistant"] {
        background: transparent !important; margin-right: auto !important;
        padding-left: 0 !important;
    }
    [data-testid="stChatMessage"] p { font-size: 0.95rem; line-height: 1.65; color: #2d2d2d; }

    /* ---- chat input ---- */
    [data-testid="stChatInput"] textarea {
        border-radius: 1rem !important; border: 1px solid #dcdcd5 !important;
        padding: 0.7rem 1rem !important; font-size: 0.95rem !important;
        background: #fff !important;
    }

    /* ---- tab buttons ---- */
    .tab-row button {
        border-radius: 0.5rem !important; border: none !important;
        font-size: 0.85rem !important; font-weight: 500 !important;
        padding: 0.4rem 0.8rem !important; height: auto !important;
    }

    /* ---- cards ---- */
    .alert-card {
        border: 1px solid #e8e8e3; border-radius: 0.75rem;
        padding: 1rem; margin: 0.5rem 0; background: #fff;
    }
    .alert-critical { border-left: 3px solid #e06060; }
    .alert-warning  { border-left: 3px solid #d4a040; }

    /* ---- assess ---- */
    .score-bubble {
        display: inline-flex; align-items: center; justify-content: center;
        width: 3.5rem; height: 3.5rem; border-radius: 50%;
        font-weight: 700; font-size: 1.2rem; color: white;
    }

    /* ---- buttons ---- */
    .stButton > button { border-radius: 0.5rem !important; border: 1px solid #dcdcd5 !important; }
    .stButton > button:focus { box-shadow: none !important; outline: none !important; }
    [data-testid="stFormSubmitButton"] button { border: 1px solid #dcdcd5 !important; }

    /* ---- metrics ---- */
    [data-testid="stMetricValue"] { font-size: 1rem !important; color: #333 !important; }
    [data-testid="stMetricLabel"] { font-size: 0.75rem !important; color: #888 !important; }
</style>
""", unsafe_allow_html=True)


def api_get(path, **params):
    r = httpx.get(f"{API}{path}", params=params, timeout=30)
    return r.json() if r.status_code == 200 else {}

def api_post(path, json=None):
    r = httpx.post(f"{API}{path}", json=json or {}, timeout=120)
    return r.json() if r.status_code == 200 else {}

def api_delete(path, **params):
    httpx.delete(f"{API}{path}", params=params, timeout=10)


# =================== SIDEBAR ===================
with st.sidebar:
    st.markdown("#### 供应商分析")

    data = api_get("/alert/watchlist")
    companies = data.get("companies", [])
    alert_data = api_get("/alert/history")
    alert_docs = alert_data.get("alerts", [])

    c1, c2 = st.columns(2)
    c1.metric("监控", len(companies))
    c2.metric("告警", len(alert_docs))

    with st.form("add_form", clear_on_submit=True):
        name = st.text_input("加入监控", placeholder="企业名称…", label_visibility="collapsed")
        if st.form_submit_button("添加", use_container_width=True):
            if name.strip():
                api_post("/alert/watch", {"company_name": name.strip()})
                st.rerun()

    uploaded = st.file_uploader("Excel 导入", type=["xlsx"], label_visibility="collapsed")
    if uploaded:
        resp = httpx.post(f"{API}/alert/watch/upload", files={"file": uploaded.getvalue()})
        if resp.status_code == 200:
            st.success(f"导入 {resp.json()['total']} 家")
            st.rerun()

    if companies:
        selected = {}
        for c in companies:
            selected[c] = st.checkbox(c, key=f"chk_{c}")
        to_remove = [n for n, ok in selected.items() if ok]
        if to_remove:
            if st.button(f"移除 {len(to_remove)} 家", type="secondary", use_container_width=True):
                for n in to_remove:
                    api_delete("/alert/watch", company_name=n)
                st.rerun()

    st.divider()
    ca, cb = st.columns(2)
    ca.button("⚡ 免费", use_container_width=True, on_click=lambda: api_post("/alert/check-all"))
    cb.button("🔄 付费", use_container_width=True, on_click=lambda: api_post("/alert/refresh-all"))
    st.caption("每日9:00免费 · 周一9:00付费")


# =================== HEADER ===================
col_title, col_new = st.columns([5, 1])
col_title.markdown("### 供应商风险分析")
if col_new.button("新对话", use_container_width=True):
    st.session_state.messages = []
    st.session_state.session_id = str(uuid.uuid4())
    st.rerun()

# ---- tabs ----
tc1, tc2, tc3 = st.columns([1, 1, 5])
active = st.session_state.active_tab
with tc1:
    if st.button("告警中心", use_container_width=True, type="primary" if active == 0 else "secondary"):
        st.session_state.active_tab = 0; st.rerun()
with tc2:
    if st.button("智能对话", use_container_width=True, type="primary" if active == 1 else "secondary"):
        st.session_state.active_tab = 1; st.rerun()
with tc3:
    if st.button("风险评估", use_container_width=True, type="primary" if active == 2 else "secondary"):
        st.session_state.active_tab = 2; st.rerun()

st.divider()


# =================== TAB: ALERTS ===================
if active == 0:
    if not alert_docs:
        st.info("暂无告警")

    for doc in alert_docs:
        sev = doc.get("severity", "warning")
        css = "alert-critical" if sev == "critical" else "alert-warning"
        ts = doc.get("created_at", "")[:16].replace("T", " ")
        changes = doc.get("changes", [])
        text = " · ".join(f"{c['field']} {c['old']} -> {c['new']}" for c in changes)
        icon = "●" if sev == "critical" else "●"
        color = "#e06060" if sev == "critical" else "#d4a040"
        st.markdown(f"""
        <div class="alert-card {css}">
            <div style="display:flex; justify-content:space-between; align-items:baseline;">
                <span style="font-weight:600; font-size:0.9rem;">
                    <span style="color:{color};">{icon}</span> {doc['company_name']}
                </span>
                <span style="font-size:0.75rem; color:#999;">{ts}</span>
            </div>
            <p style="margin:0.3rem 0 0 0; font-size:0.85rem; color:#666;">{text}</p>
        </div>
        """, unsafe_allow_html=True)

    if alert_docs and st.button("清空告警记录", type="secondary"):
        httpx.delete(f"{API}/alert/history")
        st.rerun()


# =================== TAB: CHAT ===================
elif active == 1:
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    if prompt := st.chat_input("输入问题，如：对比海康威视和宝钢的风险"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.rerun()

    if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
        last_msg = st.session_state.messages[-1]["content"]
        with st.chat_message("assistant"):
            with st.spinner(""):
                r = httpx.post(f"{API}/chat/chat", json={"message": last_msg, "session_id": st.session_state.session_id}, timeout=120)
                reply = r.json().get("reply", "请求失败") if r.status_code == 200 else f"错误 {r.status_code}"
            st.markdown(reply)
            st.session_state.messages.append({"role": "assistant", "content": reply})
        st.rerun()


# =================== TAB: ASSESS ===================
else:
    col1, col2 = st.columns([4, 1])
    name = col1.text_input("公司名称", placeholder="输入完整名称", key="assess_name", label_visibility="collapsed")
    go = col2.button("评估", type="primary", use_container_width=True)

    if go and name:
        with st.spinner(f"评估 {name}..."):
            r = httpx.post(f"{API}/risk/assess", json={"company_name": name}, timeout=60)
        if r.status_code != 200:
            st.error(r.json().get("detail", "失败"))
        else:
            data = r.json()
            score, level = data["risk_score"], data["risk_level"]
            fin = data.get("financial")
            rd = data.get("risk_detail") or {}

            color = "#2d8c63" if score <= 30 else "#d4a040" if score <= 60 else "#e06060"

            st.markdown(f"""
            <div style="background:#fff; border:1px solid #e8e8e3; border-radius:1rem; padding:1.5rem; margin-bottom:1rem;">
                <div style="display:flex; align-items:center; gap:2rem;">
                    <div style="text-align:center;">
                        <div class="score-bubble" style="background:{color};">{score}</div>
                        <span style="font-size:0.75rem; color:#999;">/100</span>
                    </div>
                    <span style="font-size:1.1rem; font-weight:600; color:{color};">{level}</span>
                    <div style="flex:1; background:#eee; height:6px; border-radius:3px;">
                        <div style="width:{score}%; height:100%; background:{color}; border-radius:3px;"></div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            c1, c2, c3, c4 = st.columns(4)
            if fin:
                c1.metric("营收增长", f"{fin['revenue_growth']*100:.1f}%")
                c2.metric("净利增长", f"{fin['net_profit_growth']*100:.1f}%")
                c3.metric("负债率", f"{fin['debt_ratio']*100:.1f}%")
                c4.metric("每股现金流", f"¥{fin['cash_flow']:.2f}")
            else:
                for c in [c1, c2, c3, c4]:
                    c.metric("—", "无数据")

            st.markdown("**风险明细**")
            r1, r2, r3 = st.columns(3)
            with r1:
                for k, lab in [("lawsuit_count","诉讼"), ("executed_count","被执行"), ("dishonesty_count","失信"), ("major_lawsuit","重大诉讼")]:
                    v = rd.get(k, 0)
                    s = "⚠️" if (k == "major_lawsuit" and v) else "✓" if k == "major_lawsuit" else ""
                    st.caption(f"{lab}  {s}  {v}")
            with r2:
                for k, lab in [("abnormal_operation_count","经营异常"), ("administrative_penalty_count","行政处罚"), ("legal_person_change_frequent","法人频繁变更")]:
                    v = rd.get(k, 0)
                    s = "⚠️" if (k == "legal_person_change_frequent" and v) else "✓" if k == "legal_person_change_frequent" else ""
                    st.caption(f"{lab}  {s}  {v}")
            with r3:
                if fin:
                    debt, cash = fin.get("debt_ratio", 0), fin.get("cash_flow", 0)
                    st.caption(f"负债率>70%  {'⚠️' if debt > 0.7 else '✓'}  {debt*100:.1f}%")
                    st.caption(f"现金为负  {'⚠️' if cash < 0 else '✓'}  ¥{cash:.2f}")
                else:
                    st.caption("无财报数据")
