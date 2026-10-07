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

NOME_EMPRESA = "EstoquIA-Transporte "
LOGO = "logo.png"
ICONE = "icone.png"
TEM_LOGO = os.path.exists(LOGO)
TEM_ICONE = os.path.exists(ICONE)

st.set_page_config(
    page_title=f"{NOME_EMPRESA} | EstoquIA",
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
MODELOS = ["gemini-3.5-flash", "gemini-3.1-flash-lite"]  # principal e reserva

@st.cache_data(ttl=60)
def carregar_estoque():
    df = pd.read_csv(URL_CSV)
    df.columns = df.columns.str.strip()
    df = df.dropna(how="all")
    return df

def abrir_resposta(mensagens):
    """Abre a resposta em streaming. Retorna (stream, modelo) ou (None, None)."""
    for modelo in MODELOS:
        for tentativa in range(2):
            try:
                stream = modelo_ia.chat.completions.create(
                    messages=mensagens,
                    model=modelo,
                    stream=True,
                    reasoning_effort="low",  # menos "pensamento" = mais rápido
                )
                return stream, modelo
            except NotFoundError:
                break  # modelo indisponível: passa para o próximo
            except (InternalServerError, RateLimitError):
                time.sleep(1)
    return None, None


estoque = carregar_estoque()

resumo = f"Total de produtos cadastrados: {len(estoque)}\n"
if "Total" in estoque.columns:
    total = pd.to_numeric(estoque["Total"], errors="coerce").sum()
    resumo += f"Valor total do estoque: R$ {total:,.2f}\n"


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
if TEM_LOGO:
    st.image(LOGO, width=260)
st.title("EstoquIA DPC")
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
        stream, modelo_usado = abrir_resposta(
            [{"role": "system", "content": instrucoes}]
            + st.session_state["lista_mensagens"]
        )
        if stream is None:
            st.error("A IA não respondeu agora. Tente de novo em alguns minutos.")
        else:
            texto_ia = st.write_stream(texto_do_stream(stream))
            st.caption(f"⏱ {time.time() - inicio:.1f}s · {modelo_usado}")
            st.session_state["lista_mensagens"].append(
                {"role": "assistant", "content": texto_ia}
            )
