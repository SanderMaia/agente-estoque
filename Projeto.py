#titulo 
#input do chat ( campo de mansagem)
#cada mensagem que o usuario enviar:
     #mostrar a mensagem que o usuario enviar no chat
    # pegar a pergunta e enviar para uma IA responder
    # exibir a resposta da IA na tela 
#streamlit run projeto.py

# Streamlit -> apenas com python cria o front-end e o backend
 # a IA que será usada : OpenAI
                   
import os
import time
import streamlit as st
import pandas as pd
from openai import OpenAI, InternalServerError, RateLimitError

NOME_EMPRESA = "DpcIA"
LOGO = "logo.png"
TEM_LOGO = os.path.exists(LOGO)

st.set_page_config(
    page_title=f"{NOME_EMPRESA} | Assistente de Estoque",
    page_icon=LOGO if TEM_LOGO else "🚚",
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

modelo_ia = OpenAI(
    api_key=st.secrets["GEMINI_API_KEY"],
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
)
URL_CSV = st.secrets["URL_CSV"]
MODELOS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]


@st.cache_data(ttl=60)
def carregar_estoque():
    df = pd.read_csv(URL_CSV)
    df.columns = df.columns.str.strip()
    df = df.dropna(how="all")
    return df


def chamar_ia(mensagens):
    for modelo in MODELOS:
        for tentativa in range(3):
            try:
                return modelo_ia.chat.completions.create(
                    messages=mensagens, model=modelo
                )
            except (InternalServerError, RateLimitError):
                time.sleep(2 * (tentativa + 1))
    return None


estoque = carregar_estoque()

instrucoes = f"""Você é o assistente de estoque da {NOME_EMPRESA}.
Você TEM acesso aos dados abaixo, que vieram da planilha de estoque.
Responda em português, de forma curta e direta, usando SOMENTE esses dados.
Se um produto não estiver na tabela, diga que não encontrou. Não invente valores.
Para totais e somas, calcule a partir da tabela.

ESTOQUE ATUAL (CSV):
{estoque.to_csv(index=False)}
"""

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

col_logo, col_titulo = st.columns([1, 5], vertical_alignment="center")
with col_logo:
    if TEM_LOGO:
        st.image(LOGO, width=80)
with col_titulo:
    st.title("Assistente de Estoque")
    st.caption("Pergunte sobre quantidades, valores, setores e movimentações.")

if "lista_mensagens" not in st.session_state:
    st.session_state["lista_mensagens"] = []

AVATAR_IA = LOGO if TEM_LOGO else "🤖"

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
        with st.spinner("Consultando o estoque..."):
            resposta_ia = chamar_ia(
                [{"role": "system", "content": instrucoes}]
                + st.session_state["lista_mensagens"]
            )
        if resposta_ia is None:
            st.error("O serviço de IA está sobrecarregado. Tente de novo em alguns minutos.")
        else:
            texto_ia = resposta_ia.choices[0].message.content
            st.write(texto_ia)
            st.session_state["lista_mensagens"].append(
                {"role": "assistant", "content": texto_ia}
            )