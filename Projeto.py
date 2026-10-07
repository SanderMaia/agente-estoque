import io
import os
import re
import time
import unicodedata
import requests
import altair as alt
import streamlit as st
import pandas as pd
from openai import OpenAI

NOME_EMPRESA = "DPCNET"
LOGO = "logo.png"
ICONE = "icone.png"
TEM_LOGO = os.path.exists(LOGO)
TEM_ICONE = os.path.exists(ICONE)
VERDE = "#3ec252"
VERMELHO = "#e5484d"

st.set_page_config(
    page_title=f"{NOME_EMPRESA} | Painel de Estoque",
    page_icon=ICONE if TEM_ICONE else "🚚",
    layout="wide",
)

st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .block-container {padding-top: 1.5rem;}
    div[data-testid="stMetric"] {
        background: #F0F4F2; border-radius: 12px; padding: 14px 16px;
        border-left: 5px solid #3ec252;
    }
    div[data-testid="stMetric"] * {color: #1A1A1A !important;}
    </style>
    """,
    unsafe_allow_html=True,
)

URL_CSV = st.secrets["URL_CSV"]

# ---------- Provedores de IA (o primeiro que responder é usado) ----------
PROVEDORES = []
if "GROQ_API_KEY" in st.secrets:
    PROVEDORES.append((
        "Groq",
        OpenAI(api_key=st.secrets["GROQ_API_KEY"],
               base_url="https://api.groq.com/openai/v1",
               timeout=30.0, max_retries=0),
        ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"],
    ))
PROVEDORES.append((
    "Gemini",
    OpenAI(api_key=st.secrets["GEMINI_API_KEY"],
           base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
           timeout=45.0, max_retries=0),
    ["gemini-3.1-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash"],
))


# ---------- Funções de apoio ----------
def norm(texto):
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def achar(df, *nomes):
    mapa = {norm(c): c for c in df.columns}
    for n in nomes:
        if norm(n) in mapa:
            return mapa[norm(n)]
    return None


def limpar_numero(v):
    if pd.isna(v):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).replace("R$", "").replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return 0.0


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@st.cache_data(ttl=60)
def carregar_estoque():
    resp = requests.get(URL_CSV, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.content.decode("utf-8")))
    df.columns = df.columns.str.strip()
    return df.dropna(how="all")


def abrir_resposta(mensagens):
    erros = []
    for nome, cliente, modelos in PROVEDORES:
        for modelo in modelos:
            try:
                stream = cliente.chat.completions.create(
                    messages=mensagens, model=modelo, stream=True
                )
                return stream, f"{nome} · {modelo}", erros
            except Exception as e:
                erros.append(f"{nome}/{modelo} -> {type(e).__name__} "
                             f"{getattr(e, 'status_code', '')}: {str(e)[:150]}")
    return None, None, erros


def texto_do_stream(stream):
    for pedaco in stream:
        if pedaco.choices and pedaco.choices[0].delta.content:
            yield pedaco.choices[0].delta.content


# ---------- Dados ----------
try:
    bruto = carregar_estoque()
except Exception:
    st.error("Não consegui ler a planilha agora. Tente de novo em instantes.")
    st.stop()

C_PROD = achar(bruto, "Produto")
C_SETOR = achar(bruto, "Setor")
C_QTD = achar(bruto, "Qtd", "Quantidade", "Quatidade")
C_UNIT = achar(bruto, "Valor Unitario")
C_TOTAL = achar(bruto, "Total")

if not (C_PROD and C_QTD and (C_UNIT or C_TOTAL)):
    st.error("Não encontrei as colunas Produto, Qtd e Valor Unitario/Total na planilha.")
    st.write("Colunas encontradas:", bruto.columns.tolist())
    st.stop()

qtd = bruto[C_QTD].apply(limpar_numero)
unit = bruto[C_UNIT].apply(limpar_numero) if C_UNIT else pd.Series(0.0, index=bruto.index)
total = bruto[C_TOTAL].apply(limpar_numero) if C_TOTAL else qtd * unit

d = pd.DataFrame({
    "produto": bruto[C_PROD].astype(str).str.strip(),
    "setor": bruto[C_SETOR].astype(str).str.strip() if C_SETOR
             else pd.Series("Geral", index=bruto.index),
    "qtd": qtd,
    "unit": unit,
    "total": total,
})
d = d[(d["produto"] != "") & (d["produto"].str.lower() != "nan")]

# ---------- Barra lateral ----------
with st.sidebar:
    if TEM_LOGO:
        st.image(LOGO, use_container_width=True)
    st.caption("Painel de estoque com IA")
    st.divider()
    setores = sorted(d["setor"].unique())
    sel = st.multiselect("Setor", setores, default=setores)
    busca = st.text_input("Buscar produto")
    limite = st.number_input("Alerta de estoque baixo (até)", min_value=0, max_value=100, value=5)
    st.divider()
    if st.button("🔄 Atualizar estoque", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    if st.button("🗑️ Limpar conversa", use_container_width=True):
        st.session_state["lista_mensagens"] = []
        st.rerun()

df_f = d[d["setor"].isin(sel)]
if busca:
    df_f = df_f[df_f["produto"].str.contains(busca, case=False, na=False)]

# ---------- Contexto da IA (sempre o estoque completo) ----------
resumo = (f"Total de produtos: {len(d)}\n"
          f"Valor total do estoque: {brl(d['total'].sum())}\n"
          f"Valor por setor:\n")
for setor, valor in d.groupby("setor")["total"].sum().sort_values(ascending=False).items():
    resumo += f"- {setor}: {brl(valor)}\n"

instrucoes = f"""Você é o assistente de estoque da {NOME_EMPRESA}.
Responda em português, em poucas linhas, usando SOMENTE os dados abaixo.
Se um produto não estiver na tabela, diga que não encontrou. Não invente valores.
Colunas: produto, setor, qtd (quantidade em estoque), unit (valor unitário em R$),
total (qtd x unit, em R$). Para totais gerais ou por setor, use o RESUMO.

RESUMO:
{resumo}
ESTOQUE ATUAL (CSV):
{d.to_csv(index=False)}
"""

# ---------- Cabeçalho ----------
col_a, col_b = st.columns([1, 4], vertical_alignment="center")
with col_a:
    if TEM_LOGO:
        st.image(LOGO, width=220)
with col_b:
    st.title("Painel de Estoque")
    st.caption("Indicadores em tempo real e assistente com IA.")

aba_painel, aba_chat = st.tabs(["📊 Painel", "💬 Assistente"])

# ================= PAINEL =================
with aba_painel:
    zerados = df_f[df_f["qtd"] <= 0]
    baixos = df_f[(df_f["qtd"] > 0) & (df_f["qtd"] <= limite)]

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Valor do estoque", brl(df_f["total"].sum()))
    k2.metric("Produtos", f"{len(df_f)}")
    k3.metric("Unidades", f"{df_f['qtd'].sum():,.0f}".replace(",", "."))
    k4.metric("Zerados", f"{len(zerados)}")
    k5.metric("Estoque baixo", f"{len(baixos)}")

    if len(zerados):
        nomes = ", ".join(zerados["produto"].head(8))
        extra = f" e mais {len(zerados) - 8}" if len(zerados) > 8 else ""
        st.warning(f"**Produtos zerados:** {nomes}{extra}")

    st.write("")
    g1, g2 = st.columns(2)

    with g1:
        st.subheader("Valor por setor")
        por_setor = df_f.groupby("setor", as_index=False)["total"].sum()
        if por_setor["total"].sum() > 0:
            rosca = alt.Chart(por_setor).mark_arc(innerRadius=70).encode(
                theta=alt.Theta("total:Q"),
                color=alt.Color("setor:N", title="Setor"),
                tooltip=[alt.Tooltip("setor:N", title="Setor"),
                         alt.Tooltip("total:Q", title="Valor (R$)", format=",.2f")],
            ).properties(height=320)
            st.altair_chart(rosca, use_container_width=True)
        else:
            st.info("Sem valores para mostrar.")

    with g2:
        st.subheader("Top 10 em valor")
        top = df_f.nlargest(10, "total")
        barras = alt.Chart(top).mark_bar(color=VERDE).encode(
            x=alt.X("total:Q", title="Valor em estoque (R$)"),
            y=alt.Y("produto:N", sort="-x", title=None),
            tooltip=[alt.Tooltip("produto:N", title="Produto"),
                     alt.Tooltip("qtd:Q", title="Quantidade"),
                     alt.Tooltip("total:Q", title="Valor (R$)", format=",.2f")],
        ).properties(height=320)
        st.altair_chart(barras, use_container_width=True)

    st.subheader("Itens mais críticos")
    criticos = df_f[df_f["qtd"] <= limite].sort_values("qtd").head(15)
    if criticos.empty:
        st.success(f"Nenhum item com estoque até {limite} unidades.")
    else:
        crit = alt.Chart(criticos).mark_bar(color=VERMELHO).encode(
            x=alt.X("produto:N", sort="y", title=None, axis=alt.Axis(labelAngle=-40)),
            y=alt.Y("qtd:Q", title="Quantidade"),
            tooltip=[alt.Tooltip("produto:N", title="Produto"),
                     alt.Tooltip("setor:N", title="Setor"),
                     alt.Tooltip("qtd:Q", title="Quantidade")],
        ).properties(height=300)
        st.altair_chart(crit, use_container_width=True)

    with st.expander("Ver estoque detalhado"):
        tabela = df_f.rename(columns={
            "produto": "Produto", "setor": "Setor", "qtd": "Qtd",
            "unit": "Valor unitário (R$)", "total": "Total (R$)"})
        st.dataframe(tabela, hide_index=True, use_container_width=True)

# ================= ASSISTENTE =================
with aba_chat:
    if "lista_mensagens" not in st.session_state:
        st.session_state["lista_mensagens"] = []

    AVATAR_IA = ICONE if TEM_ICONE else "🤖"

    for m in st.session_state["lista_mensagens"]:
        av = AVATAR_IA if m["role"] == "assistant" else None
        st.chat_message(m["role"], avatar=av).write(m["content"])

    pergunta_rapida = None
    if not st.session_state["lista_mensagens"]:
        st.write("**Experimente perguntar:**")
        c1, c2, c3 = st.columns(3)
        if c1.button("Valor total do estoque", use_container_width=True):
            pergunta_rapida = "Qual o valor total do estoque?"
        if c2.button("Itens com estoque zerado", use_container_width=True):
            pergunta_rapida = "Quais produtos estão com quantidade zerada?"
        if c3.button("Estoque por setor", use_container_width=True):
            pergunta_rapida = "Qual a quantidade e o valor total de cada setor?"

    texto_usuario = st.chat_input("Digite sua pergunta sobre o estoque...")
    if pergunta_rapida:
        texto_usuario = pergunta_rapida

    if texto_usuario:
        st.chat_message("user").write(texto_usuario)
        st.session_state["lista_mensagens"].append(
            {"role": "user", "content": texto_usuario})

        with st.chat_message("assistant", avatar=AVATAR_IA):
            inicio = time.time()
            with st.spinner("Consultando o estoque..."):
                stream, usado, erros = abrir_resposta(
                    [{"role": "system", "content": instrucoes}]
                    + st.session_state["lista_mensagens"])
            if stream is None:
                st.warning("A IA não respondeu agora. Tente de novo em alguns minutos.")
                st.code("\n".join(erros[-6:]))
            else:
                try:
                    texto_ia = st.write_stream(texto_do_stream(stream))
                    st.caption(f"⏱ {time.time() - inicio:.1f}s · {usado}")
                    st.session_state["lista_mensagens"].append(
                        {"role": "assistant", "content": texto_ia})
                except Exception as e:
                    st.error(f"A resposta foi interrompida: {type(e).__name__}")
