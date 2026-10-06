#titulo 
#input do chat ( campo de mansagem)
#cada mensagem que o usuario enviar:
     #mostrar a mensagem que o usuario enviar no chat
    # pegar a pergunta e enviar para uma IA responder
    # exibir a resposta da IA na tela 
#streamlit run projeto.py

# Streamlit -> apenas com python cria o front-end e o backend
 # a IA que será usada : OpenAI
                   
import streamlit as st
from openai import OpenAI
import pandas as pd


modelo_ia = OpenAI(
    api_key=st.secrets["GEMINI_API_KEY"],
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)


URL_CSV = st.secrets["URL_CSV"]  # link para o arquivo CSV no Google Drive


def limpar_numero(valor):
    """Converte 'R$ 1.234,50' em 1234.5. Se já for número, mantém."""
    if pd.isna(valor):
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = (str(valor).replace("R$", "").replace(" ", "")
             .replace(".", "").replace(",", "."))
    try:
        return float(texto)
    except ValueError:
        return 0.0


@st.cache_data(ttl=60)  # recarrega a planilha no máximo a cada 60 segundos
def carregar_estoque():
    df = pd.read_csv(URL_CSV)
    df.columns = df.columns.str.strip()          # tira espaços dos nomes
    df = df.dropna(how="all")                    # remove linhas vazias
    for coluna in df.columns:
        if coluna not in ("Produto", "Setor"):   # o resto é número
            df[coluna] = df[coluna].apply(limpar_numero)
    return df

estoque = carregar_estoque()
st.write("Colunas lidas:", estoque.columns.tolist())
st.dataframe(estoque.head(10))
resumo = f"""
Total de produtos cadastrados: {len(estoque)}
Valor total do estoque: R$ {estoque['Total'].sum():,.2f}
Valor total de saídas: R$ {estoque['Valor de Saída'].sum():,.2f}
Valor total de entradas: R$ {estoque['Valor de Entrada'].sum():,.2f}
"""

instrucoes = f""" Você é o assistente de estoque da minha empresa.
Responda em português, de forma curta e direta, usando SOMENTE os dados abaixo.
Se um produto não estiver na tabela, diga que não encontrou. Não invente valores.
Valores em reais (R$). Para totais gerais, use o RESUMO abaixo em vez de somar.

Colunas: Produto, Setor, Quatidade (quantidade em estoque), Valor Unitario,
Total (quantidade x valor unitário), Saida (unidades que saíram),
Valor de Saída, Entrada (unidades que entraram), Valor de Entrada, valor total do estoque do dia.

RESUMO:
{resumo}

ESTOQUE ATUAL (formato CSV):
{estoque.to_csv(index=False)}
"""

st.write("# TransporteDpcIA")

if "lista_mensagens" not in st.session_state:
    st.session_state["lista_mensagens"] = []

for mensagem in st.session_state["lista_mensagens"]:
    st.chat_message(mensagem["role"]).write(mensagem["content"])

texto_usuario = st.chat_input("Digite sua pergunta:")

if texto_usuario:
    st.chat_message("user").write(texto_usuario)
    st.session_state["lista_mensagens"].append(
        {"role": "user", "content": texto_usuario}
    )

    resposta_ia = modelo_ia.chat.completions.create(
        messages=[{"role": "system", "content": instrucoes}]
                + st.session_state["lista_mensagens"],
        model="gemini-3.5-flash-lite"
    )
    texto_ia = resposta_ia.choices[0].message.content

    st.chat_message("assistant").write(texto_ia)
    st.session_state["lista_mensagens"].append(
        {"role": "assistant", "content": texto_ia}
    )
 