import io
import os
import time
import requests
import streamlit as st
import pandas as pd
from openai import (
    OpenAI,
    InternalServerError,
    RateLimitError,
    NotFoundError,
    APITimeoutError,
    APIConnectionError,
)

NOME_EMPRESA = "DPCNET"
LOGO = "logo.png"
ICONE = "icone.png"
TEM_LOGO = os.path.exists(LOGO)
TEM_ICONE = os.path.exists(ICONE)

st.set_page_config(
    page_title=f"{NOME_EMPRESA} | Assistente de Estoque",
    page_icon=ICONE if TEM_ICONE else "🚚",
    layout="centered",
)

st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .block-container {padding-top: 2rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

# timeout: espera no máximo 25 s por tentativa; max_retries=0: sem tentativas escondidas
modelo_ia = OpenAI(
    api_key=st.secrets["GEMINI_API_KEY"],
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    timeout=25.0,
    max_retries=0,
)
URL_CSV = st.secrets["URL_CSV"]
MODELOS = ["gemini-3.1-flash-lite", "gemini-3.5-flash"]  # mais rápido primeiro


@st.cache_data(ttl=60)
def carregar_estoque():
    resp = requests.get(URL_CSV, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.content.decode("utf-8")))
    df.columns = df.columns.str.strip()
    df = df.dropna(how="all")
    return df


def abrir_resposta(mensagens):
    """Abre a resposta em streaming. Retorna (stream, modelo) ou (None, None)."""
    for modelo in MODELOS:
        for extra in ({"reasoning_effort": "low"}, {}):
            try:
                stream = modelo_ia.chat.completions.create(
                    messages=mensagens,
                    model=modelo,
                    stream=True,
                    **extra,
                )
                return stream, modelo
            except NotFoundError:
                break  # modelo indisponível: próximo modelo
            except (InternalServerError, RateLimitError, APITimeoutError, APIConnectionError):
                break  # lento ou fora do ar: não insiste, vai para o próximo modelo
            except Exception:
                continue  # parâmetro não aceito: tenta sem ele
    return None, None


def texto_do_stream(stream):
    for pedaco in stream:
        if pedaco.choices and pedaco.choices[0].delta.content:
            yield pedaco.choices[0].delta.content


try:
    estoque = carregar_estoque()
except Exception:
    st.error("Não consegui ler a planilha agora. Tente de novo em instantes.")
    st.stop()

# Totais calculados pelo pandas (rápidos e confiáveis)
resumo = f"Total de produtos cadastrados: {len(estoque)}\n"
if "Total" in estoque.columns:
    estoque["Total"] = pd.to_numeric(estoque["Total"], errors="coerce")
    resumo += f"Valor total do estoque: R$ {estoque['Total'].sum():,.2f}\n"
    if "Setor" in estoque.columns:
        por_setor = estoque.groupby("Setor")["Total"].sum().sort_values(ascending=False)
        resumo += "Valor total por setor:\n"
        for setor, valor in por_setor.items():
            resumo += f"- {setor}: R$ {valor:,.2f}\n"

instrucoes = f"""Você é o assistente de estoque da {NOME_EMPRESA}.
Responda em português, em poucas linhas, usando SOMENTE os dados abaixo.
Se um produto não estiver na tabela, diga que não encontrou. Não invente valores.
Para totais gerais ou por setor, use o RESUMO. Para outras contas, calcule a partir da tabela.

RESUMO:
{resumo}
ESTOQUE ATUAL (CSV):
{estoque.to_csv(index=False)}
"""

# --- Barra lateral ---
with st.sidebar:
    if TEM_LOGO:
        st.image(LOGO, use_container_width=True)
    st.markdown(f"### {NOME_EMPRESA}")
    st.caption("Assistente de estoque com IA")
    st.divider()
    st.metric("Produtos cadastrados", len(estoque))
    if st.button("🔄 Atualizar estoque", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    if st.button("🗑️ Limpar conversa", use_container_width=True):
        st.session_state["lista_mensagens"] = []
        st.rerun()

# --- Cabeçalho ---
if TEM_LOGO:
    st.image(LOGO, width=260)
st.title("Assistente de Estoque")
st.caption("Pergunte sobre quantidades, valores, setores e movimentações.")

if "lista_mensagens" not in st.session_state:
    st.session_state["lista_mensagens"] = []

AVATAR_IA = ICONE if TEM_ICONE else "🤖"

for mensagem in st.session_state["lista_mensagens"]:
    avatar = AVATAR_IA if mensagem["role"] == "assistant" else None
    st.chat_message(mensagem["role"], avatar=avatar).write(mensagem["content"])

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
        {"role": "user", "content": texto_usuario}
    )

    with st.chat_message("assistant", avatar=AVATAR_IA):
        inicio = time.time()
        with st.spinner("Consultando o estoque..."):
            stream, modelo_usado = abrir_resposta(
                [{"role": "system", "content": instrucoes}]
                + st.session_state["lista_mensagens"]
            )
        if stream is None:
            st.error("A IA não respondeu agora. Tente de novo em alguns minutos.")
        else:
            try:
                texto_ia = st.write_stream(texto_do_stream(stream))
                st.caption(f"⏱ {time.time() - inicio:.1f}s · {modelo_usado}")
                st.session_state["lista_mensagens"].append(
                    {"role": "assistant", "content": texto_ia}
                )
            except Exception:
                st.error("A resposta foi interrompida. Tente de novo.")
