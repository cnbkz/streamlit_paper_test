import sys, os
# Windows 환경에서 ASCII 기본 인코딩으로 인한 UnicodeEncodeError 방지 (Cloud에서는 무해)
os.environ.setdefault("PYTHONUTF8", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 로컬 .env 파일 로드 (Cloud에서는 파일 없어도 무해)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"), override=False)
except ImportError:
    pass

import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from openai import OpenAI
from datetime import datetime, timedelta
import io
import urllib.parse
import feedparser
from pypdf import PdfReader
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

st.set_page_config(
    page_title="제지 업종 AI 분석 챗봇",
    page_icon="📄",
    layout="wide",
)

PAPER_COMPANIES = {
    "한솔제지": "213500.KS",
    "무림페이퍼": "009580.KS",
    "한국제지": "014130.KS",
    "깨끗한나라": "004540.KS",
    "아세아제지": "002310.KS",
}

PERIOD_OPTIONS = {
    "1개월": "1mo",
    "3개월": "3mo",
    "6개월": "6mo",
    "1년": "1y",
    "2년": "2y",
}


@st.cache_data(ttl=3600)
def load_stock_data(tickers: dict, period: str) -> dict[str, pd.DataFrame]:
    data = {}
    for name, ticker in tickers.items():
        try:
            df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
            if not df.empty:
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
                data[name] = df
        except Exception:
            pass
    return data


@st.cache_data(ttl=3600)
def load_company_info(tickers: dict) -> dict:
    info = {}
    for name, ticker in tickers.items():
        try:
            t = yf.Ticker(ticker)
            raw = t.info
            info[name] = {
                "시가총액": raw.get("marketCap"),
                "PER": raw.get("trailingPE"),
                "PBR": raw.get("priceToBook"),
                "배당수익률": raw.get("dividendYield"),
                "52주 최고": raw.get("fiftyTwoWeekHigh"),
                "52주 최저": raw.get("fiftyTwoWeekLow"),
                "현재가": raw.get("currentPrice") or raw.get("regularMarketPrice"),
                "업종": raw.get("industry"),
            }
        except Exception:
            info[name] = {}
    return info


def build_data_summary(stock_data: dict[str, pd.DataFrame], company_info: dict) -> str:
    lines = [f"오늘 날짜: {datetime.today().strftime('%Y-%m-%d')}", ""]
    lines.append("=== 제지 업종 주요 종목 현황 ===")

    for name, df in stock_data.items():
        if df.empty:
            continue
        latest = df["Close"].iloc[-1]
        prev = df["Close"].iloc[-2] if len(df) > 1 else latest
        chg_pct = (latest - prev) / prev * 100

        start_price = df["Close"].iloc[0]
        period_return = (latest - start_price) / start_price * 100

        vol_avg = df["Volume"].mean() if "Volume" in df.columns else 0

        lines.append(f"\n[{name}]")
        lines.append(f"  현재가: {latest:,.0f}원")
        lines.append(f"  전일 대비: {chg_pct:+.2f}%")
        lines.append(f"  기간 수익률: {period_return:+.2f}%")
        lines.append(f"  평균 거래량: {vol_avg:,.0f}주")

        info = company_info.get(name, {})
        if info.get("시가총액"):
            mcap = info["시가총액"]
            lines.append(f"  시가총액: {mcap / 1e8:,.0f}억원")
        if info.get("PER"):
            lines.append(f"  PER: {info['PER']:.1f}배")
        if info.get("PBR"):
            lines.append(f"  PBR: {info['PBR']:.2f}배")
        if info.get("52주 최고"):
            lines.append(f"  52주 최고/최저: {info['52주 최고']:,.0f} / {info.get('52주 최저', 'N/A'):,.0f}원")

    lines.append("\n=== 종목 간 기간 수익률 비교 ===")
    returns = {}
    for name, df in stock_data.items():
        if not df.empty and len(df) > 1:
            r = (df["Close"].iloc[-1] - df["Close"].iloc[0]) / df["Close"].iloc[0] * 100
            returns[name] = r
    for name, r in sorted(returns.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"  {name}: {r:+.2f}%")

    return "\n".join(lines)


@st.cache_data(ttl=1800)
def fetch_news(company_name: str, max_items: int = 20) -> list[dict]:
    query = urllib.parse.quote(company_name)
    url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(url)
    results = []
    for entry in feed.entries[:max_items]:
        pub = ""
        if entry.get("published_parsed"):
            pub = datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d %H:%M")
        results.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "published": pub,
            "source": entry.get("source", {}).get("title", ""),
            "summary": entry.get("summary", ""),
        })
    return results


def build_news_summary(news_by_company: dict[str, list]) -> str:
    lines = ["=== 기업별 최신 뉴스 헤드라인 ==="]
    for company, articles in news_by_company.items():
        lines.append(f"\n[{company}]")
        for a in articles[:5]:
            lines.append(f"  - {a['published']}  {a['title']}  ({a['source']})")
    return "\n".join(lines)


def build_html_report(
    stock_data: dict,
    company_info: dict,
    all_news: dict,
    period_label: str,
    ai_summary: str = "",
) -> str:
    today = datetime.today().strftime("%Y년 %m월 %d일")

    # 수익률 계산
    returns = {}
    for name, df in stock_data.items():
        if not df.empty and len(df) > 1:
            returns[name] = (df["Close"].iloc[-1] - df["Close"].iloc[0]) / df["Close"].iloc[0] * 100

    # ── 주가 테이블 행 ──
    stock_rows = ""
    for name, df in stock_data.items():
        if df.empty:
            continue
        price = df["Close"].iloc[-1]
        prev  = df["Close"].iloc[-2] if len(df) > 1 else price
        chg   = (price - prev) / prev * 100
        pret  = returns.get(name, 0)
        info  = company_info.get(name, {})
        mcap  = f"{info['시가총액']/1e8:,.0f}억" if info.get("시가총액") else "-"
        per   = f"{info['PER']:.1f}" if info.get("PER") else "-"
        chg_color = "#d32f2f" if chg < 0 else "#1565c0" if chg > 0 else "#333"
        pret_color = "#d32f2f" if pret < 0 else "#1565c0"
        stock_rows += f"""
        <tr>
          <td style="font-weight:600">{name}</td>
          <td style="text-align:right">{price:,.0f}원</td>
          <td style="text-align:right;color:{chg_color}">{chg:+.2f}%</td>
          <td style="text-align:right;color:{pret_color}">{pret:+.2f}%</td>
          <td style="text-align:right">{mcap}</td>
          <td style="text-align:right">{per}</td>
        </tr>"""

    # ── 수익률 순위 행 ──
    rank_rows = ""
    medals = ["🥇", "🥈", "🥉", "4위", "5위"]
    for i, (name, r) in enumerate(sorted(returns.items(), key=lambda x: x[1], reverse=True)):
        color = "#d32f2f" if r < 0 else "#1565c0"
        rank_rows += f"""
        <tr>
          <td style="text-align:center">{medals[i]}</td>
          <td style="font-weight:600">{name}</td>
          <td style="text-align:right;color:{color};font-weight:700">{r:+.2f}%</td>
        </tr>"""

    # ── 뉴스 섹션 ──
    news_sections = ""
    for company, articles in all_news.items():
        items = "".join(
            f'<li style="margin:4px 0"><a href="{a["link"]}" style="color:#1565c0">{a["title"]}</a>'
            f'<span style="color:#888;font-size:12px"> — {a["source"]} {a["published"]}</span></li>'
            for a in articles[:5]
        )
        news_sections += f"""
        <h4 style="margin:16px 0 6px;color:#37474f">{company}</h4>
        <ul style="margin:0;padding-left:18px;line-height:1.7">{items}</ul>"""

    # ── AI 요약 섹션 ──
    ai_block = ""
    if ai_summary:
        ai_block = f"""
        <div style="background:#e8f5e9;border-left:4px solid #43a047;padding:14px 16px;border-radius:4px;margin-top:12px">
          <h3 style="margin:0 0 8px;color:#2e7d32">🤖 AI 종합 분석</h3>
          <p style="margin:0;line-height:1.7;white-space:pre-wrap">{ai_summary}</p>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: 'Malgun Gothic', sans-serif; color: #333; max-width: 780px; margin: 0 auto; padding: 24px; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 8px; }}
  th {{ background: #37474f; color: #fff; padding: 8px 12px; text-align: left; font-size: 13px; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid #eceff1; font-size: 14px; }}
  tr:hover td {{ background: #f5f5f5; }}
  h2 {{ color: #263238; border-bottom: 2px solid #37474f; padding-bottom: 6px; }}
  h3 {{ color: #37474f; margin: 24px 0 8px; }}
  .badge {{ display:inline-block;background:#e3f2fd;color:#1565c0;border-radius:4px;padding:2px 8px;font-size:12px;font-weight:600 }}
</style>
</head>
<body>
<h2>📄 제지 업종 주간 분석 보고서</h2>
<p>기준일: <strong>{today}</strong> &nbsp;·&nbsp; 조회 기간: <span class="badge">{period_label}</span></p>

<h3>📈 주가 현황</h3>
<table>
  <tr><th>종목</th><th style="text-align:right">현재가</th><th style="text-align:right">전일 대비</th>
      <th style="text-align:right">기간 수익률</th><th style="text-align:right">시가총액</th><th style="text-align:right">PER</th></tr>
  {stock_rows}
</table>

<h3>🏆 기간 수익률 순위</h3>
<table>
  <tr><th style="text-align:center">순위</th><th>종목</th><th style="text-align:right">수익률</th></tr>
  {rank_rows}
</table>

{ai_block}

<h3>📰 기업별 최신 뉴스</h3>
{news_sections}

<hr style="margin-top:32px;border:none;border-top:1px solid #cfd8dc">
<p style="color:#90a4ae;font-size:12px">
  본 보고서는 Yahoo Finance 데이터와 Google News를 기반으로 자동 생성되었습니다.<br>
  투자 판단의 근거로 사용하지 마십시오.
</p>
</body>
</html>"""


def send_email(
    smtp_server: str, port: int,
    sender: str, password: str,
    recipients: list[str],
    subject: str, html_body: str,
) -> tuple[bool, str]:
    try:
        msg = MIMEMultipart("alternative")
        # 한글 제목: Header()로 UTF-8 MIME 인코딩 → ASCII 오류 방지
        msg["Subject"] = Header(subject, charset="utf-8")
        msg["From"]    = formataddr(("", sender))
        msg["To"]      = ", ".join(recipients)
        # HTML 본문은 utf-8 명시
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        ctx = ssl.create_default_context()
        # 포트 465: SSL 직접 연결 / 587·기타: STARTTLS
        if port == 465:
            with smtplib.SMTP_SSL(smtp_server, port, context=ctx, timeout=15) as server:
                server.login(sender, password)
                server.sendmail(sender, recipients, msg.as_bytes())
        else:
            with smtplib.SMTP(smtp_server, port, timeout=15) as server:
                server.ehlo()
                server.starttls(context=ctx)
                server.ehlo()
                server.login(sender, password)
                server.sendmail(sender, recipients, msg.as_bytes())
        return True, "발송 성공"
    except smtplib.SMTPAuthenticationError:
        return False, "인증 실패 — 앱 비밀번호를 확인하세요."
    except smtplib.SMTPException as e:
        return False, f"SMTP 오류: {e}"
    except UnicodeEncodeError as e:
        return False, f"인코딩 오류: {e}"
    except Exception as e:
        return False, f"오류: {e}"


def extract_pdf_text(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"[{i+1}페이지]\n{text.strip()}")
    return "\n\n".join(pages)


def get_ai_response(
    client: OpenAI,
    messages: list,
    data_summary: str,
    pdf_text: str = "",
    news_summary: str = "",
) -> str:
    pdf_section = ""
    if pdf_text:
        truncated = pdf_text[:8000] + ("\n...(이하 생략)" if len(pdf_text) > 8000 else "")
        pdf_section = f"\n=== 업로드된 PDF 문서 내용 ===\n{truncated}\n"

    news_section = f"\n{news_summary}\n" if news_summary else ""

    system_prompt = f"""당신은 주식·금융 데이터 분석 및 문서 분석 전문가입니다.
아래 제공된 주가 데이터, 뉴스, PDF 문서(있을 경우)를 바탕으로 사용자 질문에 한국어로 답변하세요.
데이터에 없는 내용을 추측하거나 허위 정보를 제공하지 마세요.
수치를 인용할 때는 구체적인 숫자를 포함하세요.

{data_summary}
{news_section}{pdf_section}"""

    full_messages = [{"role": "system", "content": system_prompt}] + messages
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=full_messages,
        temperature=0.3,
        max_tokens=1500,
    )
    return response.choices[0].message.content


def render_price_chart(stock_data: dict[str, pd.DataFrame], selected: list[str]):
    fig = go.Figure()
    for name in selected:
        df = stock_data.get(name)
        if df is None or df.empty:
            continue
        normalized = df["Close"] / df["Close"].iloc[0] * 100
        fig.add_trace(go.Scatter(
            x=df.index,
            y=normalized,
            name=name,
            mode="lines",
            hovertemplate=f"{name}<br>날짜: %{{x|%Y-%m-%d}}<br>지수: %{{y:.1f}}<extra></extra>",
        ))
    fig.update_layout(
        title="기준가 100 환산 주가 추이",
        xaxis_title="날짜",
        yaxis_title="지수 (시작=100)",
        height=400,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_volume_chart(stock_data: dict[str, pd.DataFrame], selected: list[str]):
    rows = []
    for name in selected:
        df = stock_data.get(name)
        if df is None or df.empty or "Volume" not in df.columns:
            continue
        avg_vol = df["Volume"].mean()
        rows.append({"종목": name, "평균 거래량": avg_vol})
    if not rows:
        return
    df_vol = pd.DataFrame(rows).sort_values("평균 거래량", ascending=True)
    fig = px.bar(
        df_vol, x="평균 거래량", y="종목", orientation="h",
        title="평균 거래량 비교",
        labels={"평균 거래량": "거래량 (주)"},
        height=300,
    )
    st.plotly_chart(fig, use_container_width=True)


def render_metrics(stock_data: dict[str, pd.DataFrame], company_info: dict, selected: list[str]):
    cols = st.columns(len(selected))
    for col, name in zip(cols, selected):
        df = stock_data.get(name)
        info = company_info.get(name, {})
        with col:
            if df is None or df.empty:
                st.metric(name, "데이터 없음")
                continue
            price = df["Close"].iloc[-1]
            prev = df["Close"].iloc[-2] if len(df) > 1 else price
            delta = price - prev
            st.metric(
                label=name,
                value=f"{price:,.0f}원",
                delta=f"{delta:+,.0f}원 ({delta / prev * 100:+.2f}%)",
            )
            if info.get("시가총액"):
                st.caption(f"시총 {info['시가총액'] / 1e8:,.0f}억")
            if info.get("PER"):
                st.caption(f"PER {info['PER']:.1f}배")


# ── Session state 초기화 ───────────────────────────────────────────────────────

# API 키 로드 우선순위: 환경변수(.env) → st.secrets(Cloud 대시보드 설정) → 빈 문자열
_env_api_key = os.environ.get("OPENAI_API_KEY", "")
if not _env_api_key:
    try:
        _env_api_key = st.secrets.get("OPENAI_API_KEY", "")
    except Exception:
        pass

for _k, _v in {
    "api_key": _env_api_key, "pdf_text": "", "pdf_name": "",
    "messages": [], "news_messages": [],
    "email_sender": "", "email_password": "", "email_recipients": "",
    "smtp_server": "smtp.gmail.com", "smtp_port": 587,
    "report_ai_summary": "",
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ 설정")

    # ── API Key ───────────────────────────────────────────────────────────────
    st.markdown("##### 🔑 OpenAI API Key")
    _from_env = bool(_env_api_key and st.session_state.api_key == _env_api_key)
    _raw_key = st.text_input(
        "API Key",
        type="password",
        placeholder="●●●● (.env 로드됨 — 변경 시 재입력)" if _from_env else ("●●●● (저장됨 — 변경 시 재입력)" if st.session_state.api_key else "sk-..."),
        key="_sb_api_key_widget",
        label_visibility="collapsed",
    )
    if _raw_key:
        st.session_state.api_key = _raw_key

    if st.session_state.api_key:
        if _from_env and not _raw_key:
            st.success("✅ Secrets/환경변수에서 API 키 로드됨")
        else:
            st.success("✅ API 키 활성화됨")
    else:
        st.warning("API 키를 입력하세요")

    st.divider()

    # ── PDF 업로드 ─────────────────────────────────────────────────────────────
    st.markdown("##### 📎 PDF 파일 업로드")
    sb_pdf = st.file_uploader(
        "PDF 업로드", type="pdf",
        key="sb_pdf_uploader", label_visibility="collapsed",
    )
    if sb_pdf is not None and sb_pdf.name != st.session_state.pdf_name:
        with st.spinner("PDF 추출 중..."):
            try:
                extracted = extract_pdf_text(sb_pdf.read())
                if extracted.strip():
                    st.session_state.pdf_text = extracted
                    st.session_state.pdf_name = sb_pdf.name
                    st.session_state.messages = []
                    st.rerun()
                else:
                    st.warning("텍스트 추출 불가 (이미지 기반 PDF)")
            except Exception as _e:
                st.error(f"오류: {_e}")

    if st.session_state.pdf_name:
        st.success(f"📄 {st.session_state.pdf_name[:22]}{'…' if len(st.session_state.pdf_name)>22 else ''}")
        st.caption(f"{len(st.session_state.pdf_text):,}자 추출")
        col_pv, col_pd = st.columns([1, 1])
        with col_pv:
            with st.expander("미리보기"):
                st.caption(st.session_state.pdf_text[:500] + "...")
        with col_pd:
            if st.button("제거", use_container_width=True, key="sb_del_pdf"):
                st.session_state.pdf_text = ""
                st.session_state.pdf_name = ""
                st.session_state.messages = []
                st.rerun()
    else:
        st.caption("업로드하면 AI 챗봇이 내용을 참조합니다.")

    st.divider()

    # ── 데이터 설정 ────────────────────────────────────────────────────────────
    st.markdown("##### 📊 데이터 설정")
    period_label = st.selectbox("조회 기간", list(PERIOD_OPTIONS.keys()), index=2)
    period = PERIOD_OPTIONS[period_label]

    selected_companies = st.multiselect(
        "분석 종목 선택",
        list(PAPER_COMPANIES.keys()),
        default=list(PAPER_COMPANIES.keys()),
    )

    if st.button("🔄 데이터 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # ── 챗봇 버튼 위치 설정 ────────────────────────────────────────────────────
    st.markdown("##### 💬 챗봇 버튼 위치")
    chat_btn_position = st.radio(
        "위치 선택",
        ["우측 상단 고정", "우측 하단 플로팅"],
        key="chat_btn_pos",
        label_visibility="collapsed",
    )

    st.divider()
    st.caption("📊 Yahoo Finance (yfinance)")
    st.caption("🤖 GPT-4o mini")

# ── 데이터 로딩 ───────────────────────────────────────────────────────────────

if not selected_companies:
    st.warning("사이드바에서 1개 이상의 종목을 선택하세요.")
    st.stop()

with st.spinner("주가 데이터 불러오는 중..."):
    filtered_tickers = {k: v for k, v in PAPER_COMPANIES.items() if k in selected_companies}
    stock_data = load_stock_data(filtered_tickers, period)
    company_info = load_company_info(filtered_tickers)

if not stock_data:
    st.error("데이터를 불러올 수 없습니다. 잠시 후 다시 시도하세요.")
    st.stop()

data_summary = build_data_summary(stock_data, company_info)

with st.spinner("뉴스 수집 중..."):
    all_news: dict[str, list] = {c: fetch_news(c) for c in stock_data.keys()}

news_summary_ctx = build_news_summary(all_news)
api_key = st.session_state.api_key

# ── AI 챗봇 팝업 다이얼로그 ────────────────────────────────────────────────────

@st.dialog("💬 AI 챗봇", width="large")
def open_chat_popup():
    _api = st.session_state.api_key
    if not _api:
        st.warning("사이드바에서 OpenAI API 키를 먼저 입력하세요.")
        return

    _client = OpenAI(api_key=_api)
    _pdf_badge = f"📄 {st.session_state.pdf_name}" if st.session_state.pdf_name else "PDF 없음"
    st.caption(f"컨텍스트: 주가 데이터 · 뉴스 · {_pdf_badge}")

    col_info, col_clr = st.columns([5, 1])
    with col_clr:
        if st.button("초기화", key="popup_clear", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

    # 빠른 질문 (대화 없을 때)
    if not st.session_state.messages:
        POPUP_QQ = [
            "가장 수익률이 높은 종목은?",
            "각 종목의 PER을 비교해줘",
            "한솔제지 최근 주가 흐름 분석",
            "최근 뉴스 기반 업종 동향은?",
        ]
        st.markdown("**빠른 질문:**")
        qq_cols = st.columns(2)
        for i, q in enumerate(POPUP_QQ):
            with qq_cols[i % 2]:
                if st.button(q, use_container_width=True, key=f"popup_qq_{i}"):
                    st.session_state.messages.append({"role": "user", "content": q})
                    with st.spinner("분석 중..."):
                        reply = get_ai_response(
                            _client, st.session_state.messages,
                            data_summary, st.session_state.pdf_text, news_summary_ctx,
                        )
                    st.session_state.messages.append({"role": "assistant", "content": reply})

    # 대화 기록
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    _ph = "PDF · 뉴스 · 주가에 대해 질문하세요..." if st.session_state.pdf_name else "뉴스 · 주가 · 업종 동향에 대해 질문하세요..."
    if _prompt := st.chat_input(_ph, key="popup_chat_input"):
        st.session_state.messages.append({"role": "user", "content": _prompt})
        with st.chat_message("user"):
            st.markdown(_prompt)
        with st.chat_message("assistant"):
            with st.spinner("분석 중..."):
                try:
                    _reply = get_ai_response(
                        _client, st.session_state.messages,
                        data_summary, st.session_state.pdf_text, news_summary_ctx,
                    )
                    st.markdown(_reply)
                    st.session_state.messages.append({"role": "assistant", "content": _reply})
                except Exception as _e:
                    st.error(f"오류: {_e}")

# ── 플로팅 버튼 CSS ────────────────────────────────────────────────────────────

chat_btn_position = st.session_state.get("chat_btn_pos", "우측 상단 고정")

st.markdown("""
<style>
/* 우측 하단 플로팅 버튼 */
div[data-floating-chat="true"] {
    position: fixed !important;
    bottom: 2rem !important;
    right: 2rem !important;
    z-index: 99999 !important;
}
div[data-floating-chat="true"] button {
    border-radius: 50px !important;
    padding: 0.6rem 1.4rem !important;
    font-size: 1rem !important;
    font-weight: 700 !important;
    box-shadow: 0 4px 18px rgba(255,75,75,0.45) !important;
}
/* 상단 고정 버튼 강조 */
div[data-fixed-chat="true"] button {
    background: linear-gradient(135deg,#ff4b4b,#ff8c00) !important;
    color: white !important;
    font-weight: 700 !important;
    border-radius: 8px !important;
    box-shadow: 0 2px 8px rgba(255,75,75,0.35) !important;
}
</style>
""", unsafe_allow_html=True)

# ── 페이지 헤더 ────────────────────────────────────────────────────────────────

col_hd, col_btn = st.columns([8, 2])
with col_hd:
    st.title("📄 제지 업종 AI 분석 대시보드")
    st.caption("한솔제지 · 무림페이퍼 · 한국제지 · 깨끗한나라 · 아세아제지")

if chat_btn_position == "우측 상단 고정":
    with col_btn:
        st.markdown("<div data-fixed-chat='true'>", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("💬 AI 챗봇 열기", type="primary", use_container_width=True, key="chat_open_top"):
            open_chat_popup()
        st.markdown("</div>", unsafe_allow_html=True)
else:
    # 우측 하단 플로팅: 빈 컨테이너에 CSS 적용
    st.markdown("<div data-floating-chat='true'>", unsafe_allow_html=True)
    if st.button("💬 AI 챗봇", type="primary", key="chat_open_float"):
        open_chat_popup()
    st.markdown("</div>", unsafe_allow_html=True)

# ── 탭 정의 ───────────────────────────────────────────────────────────────────

tab_home, tab_chart, tab_news, tab_news_qa, tab_email = st.tabs([
    "🏠 홈",
    "📈 차트 & 지표",
    "📰 기업 뉴스",
    "🤖 뉴스 AI Q&A",
    "📧 보고서 발송",
])

# ══════════════════════════════════════════════════════════════════════════════
# 탭 1 · 홈 — 하이라이트 요약
# ══════════════════════════════════════════════════════════════════════════════

with tab_home:
    st.markdown("## 📊 제지 업종 대시보드 — 오늘의 하이라이트")
    st.caption(f"기준일: {datetime.today().strftime('%Y년 %m월 %d일')}  ·  조회 기간: {period_label}")
    st.divider()

    # ── 섹션 1: 주가 현황 카드 ────────────────────────────────────────────────
    st.markdown("#### 📈 주가 현황")
    m_cols = st.columns(len(stock_data))
    returns = {}
    for col, (name, df) in zip(m_cols, stock_data.items()):
        if df.empty:
            continue
        price  = df["Close"].iloc[-1]
        prev   = df["Close"].iloc[-2] if len(df) > 1 else price
        delta  = price - prev
        pct    = delta / prev * 100
        start  = df["Close"].iloc[0]
        period_ret = (price - start) / start * 100
        returns[name] = period_ret
        with col:
            st.metric(
                label=f"**{name}**",
                value=f"{price:,.0f}원",
                delta=f"{delta:+,.0f}원 ({pct:+.2f}%)",
            )
            info = company_info.get(name, {})
            if info.get("시가총액"):
                st.caption(f"시총 {info['시가총액']/1e8:,.0f}억")
            if info.get("PER"):
                st.caption(f"PER {info['PER']:.1f}배")

    st.divider()

    # ── 섹션 2: 수익률 순위 + 미니 차트 ──────────────────────────────────────
    col_rank, col_chart = st.columns([2, 5])

    with col_rank:
        st.markdown("#### 🏆 기간 수익률 순위")
        sorted_ret = sorted(returns.items(), key=lambda x: x[1], reverse=True)
        for rank, (name, r) in enumerate(sorted_ret, 1):
            medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][rank - 1]
            color = "green" if r >= 0 else "red"
            st.markdown(
                f"{medal} **{name}** &nbsp; "
                f"<span style='color:{color}; font-weight:bold'>{r:+.2f}%</span>",
                unsafe_allow_html=True,
            )
        best  = sorted_ret[0]
        worst = sorted_ret[-1]
        st.divider()
        st.caption(f"최고 수익: {best[0]} ({best[1]:+.2f}%)")
        st.caption(f"최저 수익: {worst[0]} ({worst[1]:+.2f}%)")

    with col_chart:
        st.markdown("#### 📉 주가 추이 (기준가 100)")
        fig_mini = go.Figure()
        for name, df in stock_data.items():
            if df.empty:
                continue
            normalized = df["Close"] / df["Close"].iloc[0] * 100
            fig_mini.add_trace(go.Scatter(
                x=df.index, y=normalized, name=name, mode="lines",
                hovertemplate=f"{name}: %{{y:.1f}}<extra></extra>",
            ))
        fig_mini.update_layout(
            height=250, margin=dict(t=10, b=10, l=10, r=10),
            hovermode="x unified",
            legend=dict(orientation="h", y=-0.3),
        )
        st.plotly_chart(fig_mini, use_container_width=True)

    st.divider()

    # ── 섹션 3: 최신 뉴스 헤드라인 ───────────────────────────────────────────
    st.markdown("#### 📰 기업별 최신 뉴스 (각 3건)")
    news_cols = st.columns(len(all_news))
    for col, (company, articles) in zip(news_cols, all_news.items()):
        with col:
            st.markdown(f"**{company}**")
            if not articles:
                st.caption("뉴스 없음")
            for a in articles[:3]:
                st.markdown(f"- [{a['title'][:28]}{'…' if len(a['title'])>28 else ''}]({a['link']})")
                st.caption(a["published"])

    st.divider()

    # ── 섹션 4: 기능 안내 카드 ────────────────────────────────────────────────
    st.markdown("#### 🗂️ 주요 기능 안내")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.info("**📈 차트 & 지표**\n\n주가 추이·거래량·PER·PBR 등 상세 지표를 차트로 확인합니다.")
    with c2:
        st.info("**📰 기업 뉴스**\n\nGoogle News 기반 실시간 뉴스를 기업별 / 시간순으로 열람합니다.")
    with c3:
        st.info("**🤖 뉴스 AI Q&A**\n\n수집된 뉴스를 바탕으로 GPT-4o mini에게 질문합니다.")
    with c4:
        st.info("**💬 AI 챗봇**\n\n주가·뉴스·PDF 문서를 통합한 종합 AI 분석 챗봇입니다.")

# ══════════════════════════════════════════════════════════════════════════════
# 탭 2 · 차트 & 지표
# ══════════════════════════════════════════════════════════════════════════════

with tab_chart:
    st.subheader("📈 주가 차트 & 지표")
    render_metrics(stock_data, company_info, list(stock_data.keys()))
    st.divider()
    render_price_chart(stock_data, list(stock_data.keys()))
    render_volume_chart(stock_data, list(stock_data.keys()))
    with st.expander("📋 원본 데이터 요약"):
        st.text(data_summary)

# ══════════════════════════════════════════════════════════════════════════════
# 탭 3 · 기업 뉴스
# ══════════════════════════════════════════════════════════════════════════════

with tab_news:
    st.subheader("📰 제지 업종 기업 뉴스")

    col_sel, col_view, col_btn = st.columns([3, 2, 1])
    with col_sel:
        news_target = st.multiselect(
            "조회 기업", list(all_news.keys()), default=list(all_news.keys()), key="news_target",
        )
    with col_view:
        view_mode = st.radio("보기 방식", ["기업별", "시간순 통합"], horizontal=True, key="news_view")
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 새로고침", use_container_width=True, key="refresh_news"):
            st.cache_data.clear()
            st.rerun()

    if news_target:
        filtered_news = {c: all_news[c] for c in news_target if c in all_news}
        total = sum(len(v) for v in filtered_news.values())
        st.caption(f"총 {total}건 (기업당 최대 20건 · 30분 캐시)")

        with st.container(height=600):
            if view_mode == "기업별":
                for company in news_target:
                    articles = filtered_news.get(company, [])
                    st.markdown(f"**{company}** — {len(articles)}건")
                    if not articles:
                        st.caption("수집된 뉴스가 없습니다.")
                    for a in articles:
                        cd, ct = st.columns([2, 8])
                        with cd:
                            st.caption(a["published"])
                            if a["source"]:
                                st.caption(f"📌 {a['source']}")
                        with ct:
                            st.markdown(f"[{a['title']}]({a['link']})")
                    st.divider()
            else:
                merged = sorted(
                    [{**a, "company": c} for c, arts in filtered_news.items() for a in arts],
                    key=lambda x: x["published"], reverse=True,
                )
                for a in merged:
                    cd, ct = st.columns([2, 8])
                    with cd:
                        st.caption(a["published"])
                        st.caption(f"🏢 {a['company']}")
                        if a["source"]:
                            st.caption(f"📌 {a['source']}")
                    with ct:
                        st.markdown(f"[{a['title']}]({a['link']})")
                    st.divider()
    else:
        st.info("조회할 기업을 선택하세요.")

# ══════════════════════════════════════════════════════════════════════════════
# 탭 4 · 뉴스 AI Q&A
# ══════════════════════════════════════════════════════════════════════════════

with tab_news_qa:
    st.subheader("🤖 뉴스 AI Q&A")
    st.caption("수집된 뉴스 헤드라인을 기반으로 GPT-4o mini가 답변합니다.")

    if not api_key:
        st.markdown("### 🔑 OpenAI API 키 입력")
        with st.form("nqa_key_form", clear_on_submit=False):
            nqa_form_key = st.text_input("API Key", type="password", placeholder="sk-...", label_visibility="collapsed")
            nqa_submitted = st.form_submit_button("확인 →", use_container_width=True, type="primary")
        st.caption("키는 이 세션에서만 사용되며 저장되지 않습니다.")
        if nqa_submitted and nqa_form_key:
            st.session_state.api_key = nqa_form_key
            st.rerun()
    else:
        news_client = OpenAI(api_key=api_key)
        news_system_prompt = (
            "당신은 제지 업종 기업 뉴스 분석 전문가입니다.\n"
            "아래 수집된 최신 뉴스 헤드라인을 근거로 사용자 질문에 한국어로 답변하세요.\n"
            "뉴스에 없는 내용은 추측하지 말고, 기사를 인용할 때는 출처와 날짜를 함께 언급하세요.\n\n"
            + news_summary_ctx
        )

        col_sum, col_clr = st.columns([3, 1])
        with col_sum:
            if st.button("전체 뉴스 요약 보기", type="primary", key="news_quick_summary"):
                with st.spinner("요약 생성 중..."):
                    try:
                        resp = news_client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[
                                {"role": "system", "content": news_system_prompt},
                                {"role": "user", "content": "기업별 주요 이슈와 전반적인 업종 동향을 요약해주세요."},
                            ],
                            temperature=0.3, max_tokens=1000,
                        )
                        st.session_state.news_messages.append({
                            "role": "assistant",
                            "content": f"**[뉴스 자동 요약]**\n\n{resp.choices[0].message.content}",
                        })
                        st.rerun()
                    except Exception as e:
                        st.error(f"요약 오류: {e}")
        with col_clr:
            if st.button("대화 초기화", key="news_clear", use_container_width=True):
                st.session_state.news_messages = []
                st.rerun()

        if not st.session_state.news_messages:
            NEWS_QUICK_Q = [
                "한솔제지 관련 주요 이슈는?",
                "부정적인 뉴스가 있나요?",
                "가장 주목할 만한 뉴스는?",
                "기업별 뉴스 한 줄 요약",
                "무림페이퍼 최근 동향은?",
                "업종 전반의 긍정적 뉴스는?",
                "실적 관련 뉴스가 있나요?",
                "주가에 영향을 줄 뉴스는?",
                "한국제지 관련 이슈는?",
                "최근 1주일 주요 뉴스 정리",
            ]
            st.markdown("**빠른 질문:**")
            for row in (NEWS_QUICK_Q[:5], NEWS_QUICK_Q[5:]):
                row_cols = st.columns(5)
                for col, q in zip(row_cols, row):
                    if col.button(q, use_container_width=True, key=f"nq_{hash(q)}"):
                        st.session_state.news_messages.append({"role": "user", "content": q})
                        with st.spinner("분석 중..."):
                            try:
                                resp = news_client.chat.completions.create(
                                    model="gpt-4o-mini",
                                    messages=[{"role": "system", "content": news_system_prompt}]
                                    + st.session_state.news_messages,
                                    temperature=0.3, max_tokens=1000,
                                )
                                st.session_state.news_messages.append({
                                    "role": "assistant",
                                    "content": resp.choices[0].message.content,
                                })
                            except Exception as e:
                                st.session_state.news_messages.append({"role": "assistant", "content": f"오류: {e}"})
                        st.rerun()

        for msg in st.session_state.news_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        if news_prompt := st.chat_input("수집된 뉴스에 대해 질문하세요...", key="news_chat_input"):
            st.session_state.news_messages.append({"role": "user", "content": news_prompt})
            with st.chat_message("user"):
                st.markdown(news_prompt)
            with st.chat_message("assistant"):
                with st.spinner("분석 중..."):
                    try:
                        resp = news_client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[{"role": "system", "content": news_system_prompt}]
                            + st.session_state.news_messages,
                            temperature=0.3, max_tokens=1000,
                        )
                        answer = resp.choices[0].message.content
                        st.markdown(answer)
                        st.session_state.news_messages.append({"role": "assistant", "content": answer})
                    except Exception as e:
                        st.error(f"오류: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# 탭 5 · 보고서 발송
# ══════════════════════════════════════════════════════════════════════════════

with tab_email:
    st.subheader("📧 보고서 작성 및 이메일 발송")
    st.caption("주가 데이터와 뉴스를 바탕으로 HTML 보고서를 생성하고 이메일로 발송합니다.")

    # ── 2단 레이아웃 ──────────────────────────────────────────────────────────
    col_left, col_right = st.columns([1, 1], gap="large")

    # ── 좌측: 발송 설정 ───────────────────────────────────────────────────────
    with col_left:
        st.markdown("#### ✉️ 발송 설정")

        with st.form("email_config_form"):
            smtp_preset = st.selectbox(
                "SMTP 서버",
                ["Gmail (smtp.gmail.com)", "Naver (smtp.naver.com)", "Daum (smtp.daum.net)", "직접 입력"],
                key="smtp_preset_sel",
            )
            SMTP_MAP = {
                "Gmail (smtp.gmail.com)":  ("smtp.gmail.com",  587),
                "Naver (smtp.naver.com)":  ("smtp.naver.com",  587),
                "Daum (smtp.daum.net)":    ("smtp.daum.net",   465),
                "직접 입력":               ("",                587),
            }
            default_server, default_port = SMTP_MAP[smtp_preset]

            if smtp_preset == "직접 입력":
                custom_server = st.text_input("SMTP 서버 주소", placeholder="smtp.example.com")
                custom_port   = st.number_input("포트", value=587, min_value=1, max_value=65535)
                final_server, final_port = custom_server, int(custom_port)
            else:
                st.caption(f"서버: `{default_server}` · 포트: `{default_port}`")
                final_server, final_port = default_server, default_port

            sender_email = st.text_input(
                "발신 이메일",
                value=st.session_state.email_sender,
                placeholder="yourname@gmail.com",
            )
            app_password = st.text_input(
                "앱 비밀번호",
                type="password",
                value=st.session_state.email_password,
                placeholder="Gmail: 설정 → 보안 → 앱 비밀번호",
                help="일반 비밀번호가 아닌 앱 비밀번호를 사용하세요.",
            )
            recipient_input = st.text_input(
                "수신 이메일",
                value=st.session_state.email_recipients,
                placeholder="a@example.com, b@example.com (쉼표로 구분)",
            )
            email_subject = st.text_input(
                "제목",
                value=f"[제지 업종] 주간 분석 보고서 — {datetime.today().strftime('%Y.%m.%d')}",
            )

            save_btn = st.form_submit_button("설정 저장", use_container_width=True)

        if save_btn:
            st.session_state.email_sender     = sender_email
            st.session_state.email_password   = app_password
            st.session_state.email_recipients = recipient_input
            st.session_state.smtp_server      = final_server
            st.session_state.smtp_port        = final_port
            st.success("✅ 설정이 저장되었습니다.")

        # 저장 상태 표시
        if st.session_state.email_sender:
            st.info(
                f"**발신:** {st.session_state.email_sender}\n\n"
                f"**수신:** {st.session_state.email_recipients or '(미설정)'}\n\n"
                f"**서버:** {st.session_state.smtp_server}:{st.session_state.smtp_port}"
            )

        st.divider()

        # ── AI 보고서 요약 생성 ───────────────────────────────────────────────
        st.markdown("#### 🤖 AI 종합 분석 (선택)")
        if not st.session_state.api_key:
            st.caption("사이드바에서 API 키를 입력하면 AI 분석 문단을 보고서에 포함할 수 있습니다.")
        else:
            if st.button("AI 분석 생성", type="secondary", use_container_width=True, key="gen_ai_summary"):
                with st.spinner("AI 분석 생성 중..."):
                    try:
                        _c = OpenAI(api_key=st.session_state.api_key)
                        # Windows 환경에서 ASCII 인코딩 오류 방지:
                        # str → UTF-8 bytes → str round-trip으로 순수 유니코드 보장
                        _sys_msg  = "당신은 주식 및 기업 분석 전문가입니다. 보고서용 분석 문단을 한국어로 작성하세요."
                        _user_msg = (
                            f"아래 데이터를 바탕으로 제지 업종 종합 투자 분석 문단을 300자 내외로 작성해주세요."
                            f"\n\n{data_summary}\n\n{news_summary_ctx}"
                        )
                        _sys_msg  = _sys_msg.encode("utf-8").decode("utf-8")
                        _user_msg = _user_msg.encode("utf-8").decode("utf-8")
                        _r = _c.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[
                                {"role": "system", "content": _sys_msg},
                                {"role": "user",   "content": _user_msg},
                            ],
                            temperature=0.4, max_tokens=600,
                        )
                        st.session_state.report_ai_summary = _r.choices[0].message.content
                        st.success("AI 분석이 생성되었습니다.")
                    except UnicodeEncodeError as _e:
                        st.error(f"인코딩 오류 (환경 문제): {_e}\n\n"
                                 "해결: 터미널에서 `set PYTHONUTF8=1` 후 앱을 재시작하세요.")
                    except Exception as _e:
                        st.error(f"AI 생성 오류: {_e}")

            if st.session_state.report_ai_summary:
                with st.expander("생성된 AI 분석 내용"):
                    st.markdown(st.session_state.report_ai_summary)
                if st.button("AI 분석 초기화", key="clear_ai_sum"):
                    st.session_state.report_ai_summary = ""
                    st.rerun()

    # ── 우측: 보고서 미리보기 + 발송 ─────────────────────────────────────────
    with col_right:
        st.markdown("#### 📋 보고서 미리보기 및 발송")

        html_report = build_html_report(
            stock_data, company_info, all_news,
            period_label, st.session_state.report_ai_summary,
        )

        with st.expander("📄 HTML 보고서 미리보기", expanded=True):
            st.components.v1.html(html_report, height=520, scrolling=True)

        st.divider()

        # ── 발송 버튼 ────────────────────────────────────────────────────────
        ready = all([
            st.session_state.email_sender,
            st.session_state.email_password,
            st.session_state.email_recipients,
            st.session_state.smtp_server,
        ])

        if not ready:
            st.warning("좌측에서 발신 이메일·앱 비밀번호·수신 이메일을 설정하고 저장하세요.")

        send_col, _ = st.columns([2, 1])
        with send_col:
            send_clicked = st.button(
                "📨 보고서 발송",
                type="primary",
                use_container_width=True,
                disabled=not ready,
                key="send_report_btn",
            )

        if send_clicked and ready:
            recipients = [r.strip() for r in st.session_state.email_recipients.split(",") if r.strip()]
            with st.spinner(f"{len(recipients)}명에게 발송 중..."):
                ok, msg = send_email(
                    smtp_server=st.session_state.smtp_server,
                    port=st.session_state.smtp_port,
                    sender=st.session_state.email_sender,
                    password=st.session_state.email_password,
                    recipients=recipients,
                    subject=email_subject if "email_subject" in dir() else f"[제지 업종] 분석 보고서 {datetime.today().strftime('%Y.%m.%d')}",
                    html_body=html_report,
                )
            if ok:
                st.success(f"✅ {', '.join(recipients)} 로 보고서가 발송되었습니다!")
                st.balloons()
            else:
                st.error(f"❌ 발송 실패 — {msg}")

        st.divider()
        st.markdown("##### 💡 Gmail 앱 비밀번호 발급 방법")
        st.markdown("""
1. [Google 계정 보안](https://myaccount.google.com/security) 접속
2. **2단계 인증** 활성화
3. 검색창에 **앱 비밀번호** 검색 → 생성
4. 생성된 16자리 비밀번호를 위 입력란에 붙여넣기
        """)

