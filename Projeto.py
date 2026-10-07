import io
import os
import time
import requests
import streamlit as st
import pandas as pd
from openai import (
    OpenAI,
    BadRequestError,
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

modelo_ia = OpenAI(
    api_key=st.secrets["GEMINI_API_KEY"],
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    timeout=60.0,
    max_retries=0,
)
URL_CSV = st.secrets["URL_CSV"]

MODELOS_PREFERIDOS = ["gemini-3.1-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash"]
EXCLUIR = ("image", "live", "audio", "tts", "embedding", "vision", "robotics", "computer")
LIMITE_TOTAL = 120  # segundos máximos por pergunta


@st.cache_data(ttl=600)
def listar_modelos():
    try:
        nomes = [m.id.replace("models/", "") for m in modelo_ia.models.list()]
        return nomes, ""
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:300]}"


def escolher_modelos():
    disponiveis, erro = listar_modelos()
    if not disponiveis:
        return list(MODELOS_PREFERIDOS), erro
    flash = [n for n in disponiveis if "flash" in n and not any(x in n for x in EXCLUIR)]
    escolhidos = [m for m in MODELOS_PREFERIDOS if m in disponiveis]
    escolhidos += [n for n in sorted(flash, reverse=True) if n not in escolhidos]
    return escolhidos[:3], ""


@st.cache_data(ttl=60)
def carregar_estoque():
    resp = requests.get(URL_CSV, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.content.decode("utf-8")))
    df.columns = df.columns.str.strip()
    df = df.dropna(how="all")
    return df


def descrever_erro(modelo, e):
    codigo = getattr(e, "status_code", "")
    return f"{modelo} -> {type(e).__name__} {codigo}: {str(e)[:200]}"


def abrir_resposta(mensagens, modelos):
    """Duas rodadas pelos modelos, com limite total de tempo.
    Retorna (stream, modelo_usado, erros)."""
    erros = []
    inicio = time.time()
    for rodada in range(2):
        for modelo in modelos:
            if time.time() - inicio > LIMITE_TOTAL:
                return None, None, erros
            for extra in ({"reasoning_effort": "low"}, {}):
                try:
                    stream = modelo_ia.chat.completions.create(
                        messages=mensagens, model=modelo, stream=True, **extra
                    )
                    return stream, modelo, erros
                except BadRequestError as e:
                    erros.append(descrever_erro(modelo, e))
                    continue  # parâmetro não aceito: tenta sem ele
                except (NotFoundError, InternalServerError, RateLimitError,
                        APITimeoutError, APIConnectionError) as e:
                    erros.append(descrever_erro(modelo, e))
                    break  # próximo modelo
                except Exception as e:
                    erros.append(descrever_erro(modelo, e))
                    break
        time.sleep(3)
    return None, None, erros


def texto_do_stream(stream):
    for pedaco in stream:
        if pedaco.choices and pedaco.choices[0].delta.content:
            yield pedaco.choices[0].delta.content


def testar_modelos(modelos):
    """Manda um pedido mínimo a cada modelo e mede o tempo."""
    saida = []
    for m in modelos:
        t = time.time()
        try:
            modelo_ia.chat.completions.create(
                messages=[{"role": "user", "content": "Responda só: ok"}], model=m
            )
            saida.append(f"{m}: OK em {time.time() - t:.1f}s")
        except Exception as e:
            saida.append(f"{m}: {type(e).__name__} após {time.time() - t:.1f}s")
    return saida


try:
    estoque = carregar_estoque()
except Exception:
    st.error("Não consegui ler a planilha agora. Tente de novo em instantes.")
    st.stop()

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

MODELOS, erro_lista = escolher_modelos()

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
    with st.expander("Diagnóstico"):
        st.write("Modelos que serão usados:", MODELOS)
        if erro_lista:
            st.write("Não consegui listar os modelos:", erro_lista)
        if st.button("Testar velocidade da IA", use_container_width=True):
            with st.spinner("Testando (pode levar até 3 minutos)..."):
                for linha in testar_modelos(MODELOS):
                    st.write(linha)

# --- Cabeçalho ---
if TEM_LOGO:
    st.image(LOGO, width=260)
st.title("EstoqIA-DPC")
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
        with st.spinner("Consultando o estoque... se o Google estiver sobrecarregado, pode levar até 2 minutos."):
            stream, modelo_usado, erros = abrir_resposta(
                [{"role": "system", "content": instrucoes}]
                + st.session_state["lista_mensagens"],
                MODELOS,
            )
        if stream is None:
            st.warning(
                "O serviço de IA do Google está lento ou sobrecarregado agora. "
                "Tente de novo em alguns minutos."
            )
            st.code("\n".join(erros[-6:]) or "Nenhum modelo disponível para esta chave.")
        else:
            try:
                texto_ia = st.write_stream(texto_do_stream(stream))
                st.caption(f"⏱ {time.time() - inicio:.1f}s · {modelo_usado}")
                st.session_state["lista_mensagens"].append(
                    {"role": "assistant", "content": texto_ia}
                )
            except Exception as e:
                st.error(f"A resposta foi interrompida: {type(e).__name__}")
