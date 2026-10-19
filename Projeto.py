import base64
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
LOGO_TEMA_CLARO = "logo_tema_claro.png"    # texto escuro, para fundo claro
LOGO_TEMA_ESCURO = "logo_tema_escuro.png"  # texto branco, para fundo escuro
ICONE = "icone.png"
LOGO_SVG = "logo_dpc.svg"                  # logo original (a parte branca troca de cor com o tema)
TEM_SVG = os.path.exists(LOGO_SVG)
TEM_LOGO = TEM_SVG or (os.path.exists(LOGO_TEMA_CLARO) and os.path.exists(LOGO_TEMA_ESCURO))
TEM_ICONE = os.path.exists(ICONE)
VERDE = "#3ec252"
VERMELHO = "#e5484d"
AZUL = "#2F5496"
DIAS_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def logo_atual():
    """Devolve a logo certa para o tema (claro ou escuro) em uso."""
    try:
        escuro = st.context.theme.type == "dark"
    except Exception:
        escuro = False
    return LOGO_TEMA_ESCURO if escuro else LOGO_TEMA_CLARO


@st.cache_data
def camadas_logo(caminho):
    """Separa o SVG em duas camadas: a verde (fixa) e a que era branca (muda com o tema).
    Retorna (verde_base64, variavel_base64, proporcao)."""
    svg = open(caminho, encoding="utf-8").read()
    svg = re.sub(r"<\?xml[^>]*\?>\s*", "", svg)
    svg = re.sub(r"<svg\b[^>]*>", lambda m: re.sub(r'\s(?:width|height)="[^"]*"', "", m.group(0)),
                 svg, count=1)  # o tamanho vem só do viewBox
    cls = re.search(r"\.(cls-\d+)\s*\{[^}]*fill:\s*(?:#fff(?:fff)?|white)\s*;", svg, re.I)
    vb = re.search(r'viewBox="\s*[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)\s*"', svg)
    if not cls or not vb:
        raise ValueError("SVG fora do formato esperado")
    marca = f'class="{cls.group(1)}"'
    elementos = re.findall(r"<(?:path|polygon)\b[^>]*?/>", svg, flags=re.S)
    verde, variavel = svg, svg
    for e in elementos:
        if marca in e:
            verde = verde.replace(e, "")
        else:
            variavel = variavel.replace(e, "")
    variavel = re.sub(r"(\." + cls.group(1) + r"\s*\{[^}]*fill:\s*)[^;]+;", r"\g<1>#000;", variavel)
    b64 = lambda t: base64.b64encode(t.encode("utf-8")).decode("ascii")
    return b64(verde), b64(variavel), float(vb.group(1)) / float(vb.group(2))


def mostrar_logo(largura=240):
    """Mostra a logo. A parte que era branca usa a cor do texto do tema (escura no tema claro,
    clara no tema escuro), então a logo muda na hora, sem recarregar a página."""
    if TEM_SVG:
        try:
            verde, variavel, prop = camadas_logo(LOGO_SVG)
            mascara = f"url('data:image/svg+xml;base64,{variavel}') no-repeat center/contain"
            st.html(
                f'<div style="position:relative;width:100%;max-width:{largura}px;aspect-ratio:{prop:.4f};">'
                f'<div style="position:absolute;inset:0;background-color:currentColor;'
                f'-webkit-mask:{mascara};mask:{mascara};"></div>'
                f'<div style="position:absolute;inset:0;'
                f"background:url('data:image/svg+xml;base64,{verde}') no-repeat center/contain;\"></div>"
                f"</div>"
            )
            return
        except Exception:
            pass  # cai para as imagens PNG
    st.image(logo_atual(), width=largura)


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
URL_HIST = st.secrets.get("URL_HISTORICO", "")
URL_LANC = st.secrets.get("URL_LANCAMENTOS", "")   # entradas digitadas na planilha
NOMES_SAIDAS = {"ELETRICA": "Elétrica", "MECANICA": "Mecânica"}


def fontes_de_saida():
    """Todo segredo que começa com URL_SAIDAS é um formulário de saída.
    Ex.: URL_SAIDAS_ELETRICA, URL_SAIDAS_MECANICA, URL_SAIDAS_PINTURA..."""
    fontes, vistos = [], set()
    for chave in sorted(st.secrets.keys()):
        valor = st.secrets[chave]
        if not (chave.upper().startswith("URL_SAIDAS") and isinstance(valor, str) and valor.strip()):
            continue
        link = valor.strip()
        if link in vistos:  # mesmo link cadastrado duas vezes: conta uma só
            continue
        vistos.add(link)
        sufixo = chave[len("URL_SAIDAS"):].strip("_ ").upper()
        nome = NOMES_SAIDAS.get(sufixo, sufixo.replace("_", " ").title())
        fontes.append((link, f"Saídas ({nome})" if nome else "Saídas (formulário)", "Saída"))
    return fontes


FONTES_SAIDA = fontes_de_saida()

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


def para_numero(v):
    """Converte para número; devolve NaN se não for número."""
    if pd.isna(v):
        return float("nan")
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).replace("R$", "").replace(" ", "").strip()
    if t == "":
        return float("nan")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return float("nan")


def parse_datas(serie):
    """Converte textos como 15/10/2026 14:30 em datas (dia antes do mês)."""
    try:
        return pd.to_datetime(serie, dayfirst=True, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        return pd.to_datetime(serie, dayfirst=True, errors="coerce")


def achar_data(df):
    """Procura uma coluna de data (nome com 'data', 'date' ou 'dia')."""
    for c in df.columns:
        if re.search(r"\b(data|date|dia)\b", norm(c)):
            conv = parse_datas(df[c])
            if conv.notna().mean() >= 0.5:
                return c, conv
    return None, None


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def delta_brl(v):
    return ("-" if v < 0 else "+") + brl(abs(v))


def un(v):
    return f"{v:,.0f}".replace(",", ".")


def dmy(ts):
    return pd.Timestamp(ts).strftime("%d/%m/%Y")


@st.cache_data(ttl=60)
def carregar_estoque():
    resp = requests.get(URL_CSV, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.content.decode("utf-8")))
    df.columns = [str(c).strip() for c in df.columns]
    return df.dropna(how="all")


@st.cache_data(ttl=60)
def carregar_lancamentos(url):
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.content.decode("utf-8")))
    df.columns = [str(c).strip() for c in df.columns]
    return df.dropna(how="all")


@st.cache_data(ttl=300)
def carregar_historico(url):
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    h = pd.read_csv(io.StringIO(resp.content.decode("utf-8")))
    h.columns = [str(c).strip() for c in h.columns]
    c_data = achar(h, "Data")
    if not c_data:
        return pd.DataFrame()
    out = pd.DataFrame({"data": parse_datas(h[c_data])})
    for nome, chave in [("Valor Estoque", "estoque"), ("Valor Entradas", "entradas"),
                        ("Valor Saidas", "saidas"), ("Unidades", "unidades"),
                        ("Produtos", "produtos")]:
        c = achar(h, nome)
        out[chave] = h[c].apply(para_numero) if c else float("nan")
    out = out.dropna(subset=["data"]).sort_values("data").drop_duplicates("data", keep="last")
    return out.reset_index(drop=True)


def achar_flex(df, exatos, contem):
    """Acha uma coluna pelo nome exato; se não achar, por parte do nome."""
    c = achar(df, *exatos)
    if c:
        return c
    for col in df.columns:
        n = norm(col)
        if any(re.search(t, n) for t in contem):
            return col
    return None


def preparar_movimentos(bruto_m, base_prod, origem, tipo_fixo=None):
    """Valida os registros de uma origem (entradas digitadas na aba lancamentos ou respostas
    do formulário de saída) e liga cada um a um produto da aba bi.
    Retorna (mov, avisos, diag)."""
    avisos = []
    c_data = achar_flex(bruto_m, ["Data", "Data Hora", "Carimbo de data/hora"], [r"\bdata\b", "carimbo"])
    c_prod = achar_flex(bruto_m, ["Produto"], ["produto", r"\bitem\b", "itens", "material", "peca"])
    c_qtd = achar_flex(bruto_m, ["Quantidade", "Qtd"], ["quantidade", "qtde", r"\bqtd\b", "unidades"])
    c_resp = achar_flex(bruto_m, ["Responsavel"], ["responsavel", r"\bnome\b", "solicitante", "retirado"])
    c_tipo = achar(bruto_m, "Tipo") if tipo_fixo is None else None
    diag = {"origem": origem, "linhas": len(bruto_m), "colunas": list(bruto_m.columns),
            "data": c_data, "produto": c_prod, "quantidade": c_qtd}
    faltam = [n for n, c in [("data", c_data), ("produto", c_prod), ("quantidade", c_qtd)] if not c]
    if faltam:
        return None, [f"{origem}: não achei a coluna de {', '.join(faltam)}. "
                      f"Colunas lidas: {', '.join(map(str, bruto_m.columns))}."], diag

    if tipo_fixo:
        tipo = pd.Series(tipo_fixo, index=bruto_m.index)
    elif c_tipo:
        tipo = bruto_m[c_tipo].astype(str).map(norm).map(
            lambda x: "Saída" if x.startswith("sai") else "Entrada")
    else:
        tipo = pd.Series("Entrada", index=bruto_m.index)

    l = pd.DataFrame({
        "data": parse_datas(bruto_m[c_data]),
        "produto_digitado": bruto_m[c_prod].astype(str).str.strip(),
        "tipo": tipo,
        "qtd": bruto_m[c_qtd].apply(para_numero),
        "responsavel": bruto_m[c_resp].astype(str).str.strip() if c_resp
                       else pd.Series("", index=bruto_m.index),
        "origem": origem,
    })
    l["linha"] = l.index + 2  # linha na planilha (a 1 é o cabeçalho)
    l = l[(l["produto_digitado"] != "") & (l["produto_digitado"].str.lower() != "nan")]
    l["chave"] = l["produto_digitado"].map(norm)

    motivos = {
        "sem data": l["data"].isna(),
        "quantidade vazia ou menor que 1": ~(l["qtd"] > 0),
        "produto não encontrado na aba bi": ~l["chave"].isin(base_prod.index),
    }
    invalido = pd.Series(False, index=l.index)
    for nome, cond in motivos.items():
        if cond.any():
            linhas = ", ".join(str(x) for x in l.loc[cond, "linha"].head(10))
            avisos.append(f"{origem}: {int(cond.sum())} registro(s) ignorado(s) — {nome} (linhas: {linhas})")
        invalido |= cond
    mov = l[~invalido].copy()
    mov["produto"] = mov["chave"].map(base_prod["produto"])
    mov["setor"] = mov["chave"].map(base_prod["setor"])
    mov["unit"] = mov["chave"].map(base_prod["unit"])
    mov["dia"] = mov["data"].dt.normalize()
    mov["sinal"] = mov["tipo"].map({"Entrada": 1.0, "Saída": -1.0})
    mov["net"] = mov["sinal"] * mov["qtd"]
    mov["valor"] = mov["qtd"] * mov["unit"]
    return mov.reset_index(drop=True), avisos, diag


def estoque_por_dia(mov, qtd_atual, unit, total_atual, hoje):
    """Reconstrói, para o FIM de cada dia, a quantidade e o valor de cada produto, andando para
    trás a partir do estoque atual da aba bi.
    O valor parte do TOTAL ATUAL (coluna Total da bi) e só desfaz o valor das movimentações
    posteriores (quantidade x valor unitário). Assim, hoje o valor é exatamente o da bi.
    Retorna (qtd_dia, net_dia, valor_dia): tabelas dia x produto."""
    ini = min(mov["dia"].min(), hoje)
    fim = max(hoje, mov["dia"].max())
    dias = pd.date_range(ini, fim, freq="D")
    net = (mov.pivot_table(index="dia", columns="chave", values="net", aggfunc="sum")
              .reindex(index=dias, columns=qtd_atual.index).fillna(0.0))
    depois = net.iloc[::-1].cumsum().iloc[::-1].shift(-1).fillna(0.0)  # movimentos após o dia
    qtd_dia = depois.mul(-1.0).add(qtd_atual, axis=1)
    unit = unit.reindex(qtd_atual.index).fillna(0.0)
    total_atual = total_atual.reindex(qtd_atual.index).fillna(0.0)
    valor_dia = depois.mul(unit, axis=1).mul(-1.0).add(total_atual, axis=1)
    return qtd_dia, net, valor_dia


def serie_diaria(mov_p, dias, coluna):
    g = mov_p.pivot_table(index="dia", columns="tipo", values=coluna, aggfunc="sum")
    g = g.reindex(dias).fillna(0.0)
    for t in ("Entrada", "Saída"):
        if t not in g.columns:
            g[t] = 0.0
    return g[["Entrada", "Saída"]]


def linhas_periodo(m, limite):
    """Texto: por produto, quantas unidades entraram e saíram."""
    g = m.pivot_table(index="produto", columns="tipo", values="qtd", aggfunc="sum").fillna(0.0)
    for c in ("Entrada", "Saída"):
        if c not in g.columns:
            g[c] = 0.0
    g["tot"] = g["Entrada"] + g["Saída"]
    t = ""
    for prod, r in g.sort_values("tot", ascending=False).head(limite).iterrows():
        t += f"- {prod}: entraram {un(r['Entrada'])} | saíram {un(r['Saída'])}\n"
    return t


def texto_periodo(m, nome, a, b, limite):
    t = f"\n{nome} ({dmy(a)} a {dmy(b)}):"
    m = m[(m["dia"] >= a) & (m["dia"] <= b)]
    if m.empty:
        return t + " nenhuma movimentação.\n"
    ent = m[m["tipo"] == "Entrada"]
    sai = m[m["tipo"] == "Saída"]
    t += (f" entraram {un(ent['qtd'].sum())} un ({brl(ent['valor'].sum())}), "
          f"saíram {un(sai['qtd'].sum())} un ({brl(sai['valor'].sum())}).\n")
    org = sai.groupby("origem")["qtd"].sum()
    if len(org) > 1:
        t += "  Saídas por formulário: " + "; ".join(f"{o}: {un(q)} un" for o, q in org.items()) + ".\n"
    return t + linhas_periodo(m, limite)


def contexto_movimentos(mov, qtd_dia, valor_dia_prod, base_prod, hoje, p_ini, p_fim, dia_sel):
    """Texto com a movimentação por data, para a IA responder perguntas com datas."""
    if mov is None or mov.empty:
        return ""
    unit = base_prod["unit"]
    valor_dia = valor_dia_prod.sum(axis=1)
    ini_semana = hoje - pd.Timedelta(days=hoje.weekday())
    t = (f"\nHOJE é {DIAS_SEMANA[hoje.weekday()]}, {dmy(hoje)}. As movimentações com data (entradas "
         f"lançadas na planilha e saídas do formulário) vão de {dmy(mov['dia'].min())} a {dmy(mov['dia'].max())}.\n"
         "Valores em R$ usam o valor unitário atual. 'entraram'/'saíram' = unidades.\n")

    t += "\n### PERÍODO ESCOLHIDO PELO USUÁRIO NO MENU LATERAL"
    t += texto_periodo(mov, "PERÍODO ESCOLHIDO", p_ini, p_fim, 120)
    t += f"\n### DIA ESCOLHIDO PELO USUÁRIO NO MENU LATERAL: {dmy(dia_sel)} ({DIAS_SEMANA[dia_sel.weekday()]})\n"
    if dia_sel in qtd_dia.index:
        q = qtd_dia.loc[dia_sel]
        m_dia = mov[mov["dia"] == dia_sel]
        t += (f"Valor do estoque ao fim desse dia: {brl(float(valor_dia.loc[dia_sel]))}; "
              f"unidades em estoque: {un(q.sum())}; "
              f"entradas do dia {brl(m_dia.loc[m_dia['tipo'] == 'Entrada', 'valor'].sum())}; "
              f"saídas do dia {brl(m_dia.loc[m_dia['tipo'] == 'Saída', 'valor'].sum())}.\n")
        t += "Quantidade de cada produto ao fim desse dia:\n"
        for ch in q.index:
            t += f"- {base_prod.loc[ch, 'produto']}: {un(q[ch])}\n"

    t += "\n### OUTROS PERÍODOS (calculados)"
    periodos = [
        ("ONTEM", hoje - pd.Timedelta(days=1), hoje - pd.Timedelta(days=1)),
        ("ÚLTIMOS 7 DIAS", hoje - pd.Timedelta(days=6), hoje),
        ("SEMANA PASSADA (segunda a domingo)", ini_semana - pd.Timedelta(days=7),
         ini_semana - pd.Timedelta(days=1)),
        ("ÚLTIMOS 30 DIAS", hoje - pd.Timedelta(days=29), hoje),
        ("MÊS ATUAL", hoje.replace(day=1), hoje),
    ]
    for nome, a, b in periodos:
        t += texto_periodo(mov, nome, a, b, 30)

    t += ("\n### POR DIA (últimos 31 dias): data | entradas R$ | saídas R$ | valor do estoque ao fim do dia R$\n")
    for dia in pd.date_range(hoje - pd.Timedelta(days=30), hoje):
        m = mov[mov["dia"] == dia]
        e = m.loc[m["tipo"] == "Entrada", "valor"].sum()
        s = m.loc[m["tipo"] == "Saída", "valor"].sum()
        v = valor_dia.get(dia)
        t += (f"{dmy(dia)} ({DIAS_SEMANA[dia.weekday()]}) | {e:,.2f} | {s:,.2f} | "
              f"{('%.2f' % v) if v is not None and pd.notna(v) else 'sem dado'}\n")
    return t


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
            # o Streamlit trata $...$ como fórmula matemática e bagunça "R$ 1.000 ... R$ 2.000"
            yield pedaco.choices[0].delta.content.replace("$", "\\$")


# ---------- Dados: lê TODAS as colunas da aba bi ----------
try:
    bruto = carregar_estoque()
except Exception as e:
    st.error("Não consegui ler a planilha agora.")
    st.code(f"{type(e).__name__}: {str(e)[:200].replace(URL_CSV, '[link]')}")
    st.stop()

tudo = bruto.copy()
tudo = tudo.dropna(axis=1, how="all")

C_PROD = achar(tudo, "Produto")
C_SETOR = achar(tudo, "Setor")
C_QTD = achar(tudo, "Quantidade", "Qtd", "Quatidade")
C_UNIT = achar(tudo, "Valor Unitario")
C_TOTAL = achar(tudo, "Total")
C_SAIDA = achar(tudo, "Saida")
C_VSAIDA = achar(tudo, "Valor de Saida")
C_ENTRADA = achar(tudo, "Entrada")
C_VENTRADA = achar(tudo, "Valor de Entrada")

if not (C_PROD and C_QTD and (C_UNIT or C_TOTAL)):
    st.error("Não encontrei as colunas Produto, Quantidade e Valor Unitario/Total.")
    st.write("Colunas encontradas:", tudo.columns.tolist())
    st.stop()

C_DATA, serie_datas = achar_data(tudo)
if C_DATA:
    tudo[C_DATA] = serie_datas.dt.strftime("%d/%m/%Y").fillna("")

colunas_numericas = []
for c in tudo.columns:
    if c in (C_PROD, C_SETOR, C_DATA):
        continue
    conv = tudo[c].apply(para_numero)
    preenchidos = tudo[c].notna().sum()
    if preenchidos and conv.notna().sum() / preenchidos >= 0.8:
        tudo[c] = conv.fillna(0.0)
        colunas_numericas.append(c)


def num(coluna):
    if coluna is None:
        return pd.Series(0.0, index=tudo.index)
    return tudo[coluna].astype(float)


qtd = num(C_QTD)
unit = num(C_UNIT)
total = num(C_TOTAL) if C_TOTAL else qtd * unit

d = pd.DataFrame({
    "produto": tudo[C_PROD].astype(str).str.strip(),
    "setor": tudo[C_SETOR].astype(str).str.strip() if C_SETOR
             else pd.Series("Geral", index=tudo.index),
    "qtd": qtd,
    "unit": unit,
    "total": total,
    "saida": num(C_SAIDA),
    "valor_saida": num(C_VSAIDA),
    "entrada": num(C_ENTRADA),
    "valor_entrada": num(C_VENTRADA),
})
if C_DATA:
    d["data"] = serie_datas
d = d[(d["produto"] != "") & (d["produto"].str.lower() != "nan")]
tudo = tudo.loc[d.index]
d["chave"] = d["produto"].map(norm)
base_prod = d.drop_duplicates("chave").set_index("chave")

HOJE = pd.Timestamp.now(tz="America/Sao_Paulo").tz_localize(None).normalize()

_soma_total = float(d["total"].sum())
_soma_qu = float((d["qtd"] * d["unit"]).sum())
AVISO_TOTAL = ""
if _soma_total and abs(_soma_total - _soma_qu) / abs(_soma_total) > 0.01:
    AVISO_TOTAL = (f"A soma da coluna Total da aba bi ({brl(_soma_total)}) é diferente de "
                   f"Quantidade × Valor Unitario ({brl(_soma_qu)}). O valor do estoque usa a coluna Total; "
                   "as movimentações usam Quantidade × Valor Unitario. Confira essas colunas na planilha.")

# ---------- Movimentações com data: entradas (aba lancamentos) + saídas (formulário) ----------
mov = None
avisos_lanc = []
erros_fontes = []
diags = []
qtd_dia_all = None
net_dia_all = None
partes = []
FONTES = ([(URL_LANC, "Entradas (aba lancamentos)", None)] if URL_LANC else []) + FONTES_SAIDA
for url_, origem_, tipo_fixo_ in FONTES:
    try:
        bruto_m = carregar_lancamentos(url_)
        m_, av_, dg_ = preparar_movimentos(bruto_m, base_prod, origem_, tipo_fixo_)
        avisos_lanc += av_
        diags.append(dg_)
        if m_ is not None and not m_.empty:
            partes.append(m_)
    except Exception as e:
        erros_fontes.append(f"{origem_}: {type(e).__name__}: {str(e)[:150].replace(url_, '[link]')}")
if partes:
    mov = pd.concat(partes, ignore_index=True)
    qtd_dia_all, net_dia_all, valor_dia_all = estoque_por_dia(
        mov, base_prod["qtd"], base_prod["unit"], base_prod["total"], HOJE)

# ---------- Histórico diário (aba "historico", opcional) ----------
historico = pd.DataFrame()
erro_hist = ""
if URL_HIST:
    try:
        historico = carregar_historico(URL_HIST)
    except Exception as e:
        erro_hist = f"{type(e).__name__}: {str(e)[:150].replace(URL_HIST, '[link]')}"

# ---------- Barra lateral ----------
p_ini = p_fim = dia_sel = None
with st.sidebar:
    if TEM_LOGO:
        mostrar_logo(240)
    st.caption("Painel de estoque com IA")
    st.divider()
    setores = sorted(d["setor"].unique())
    sel = st.multiselect("Setor", setores, default=setores)
    busca = st.text_input("Buscar produto")
    limite = st.number_input("Alerta de estoque baixo (até)", min_value=0, max_value=100, value=5)
    if mov is not None:
        st.divider()
        st.markdown("**Consulta por data**")
        ini_hist = qtd_dia_all.index.min().date()
        fim_hist = qtd_dia_all.index.max().date()
        padrao_ini = max(ini_hist, (HOJE - pd.Timedelta(days=29)).date())
        per = st.date_input("Período (entradas e saídas)", value=(padrao_ini, fim_hist),
                            min_value=ini_hist, max_value=fim_hist, format="DD/MM/YYYY", key="periodo")
        if isinstance(per, (list, tuple)) and len(per) == 2:
            p_ini, p_fim = pd.Timestamp(per[0]), pd.Timestamp(per[1])
        else:
            p_ini, p_fim = pd.Timestamp(padrao_ini), pd.Timestamp(fim_hist)
        dia_sel = pd.Timestamp(st.date_input(
            "Dia (valor do estoque)", value=min(fim_hist, HOJE.date()),
            min_value=ini_hist, max_value=fim_hist, format="DD/MM/YYYY", key="dia_sel"))
        st.caption("Vale para os gráficos de linhas, para a aba Movimentação e para o Assistente.")
    st.divider()
    if st.button("🔄 Atualizar estoque", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    if st.button("🗑️ Limpar conversa", width="stretch"):
        st.session_state["lista_mensagens"] = []
        st.rerun()

df_f = d[d["setor"].isin(sel)]
if busca:
    df_f = df_f[df_f["produto"].str.contains(busca, case=False, na=False)]

ESCALA = alt.Scale(domain=["Entrada", "Saída"], range=[VERDE, AZUL])
if mov is not None:
    chaves_f = df_f["chave"].drop_duplicates()
    chaves_f = chaves_f[chaves_f.isin(base_prod.index)]
    mov_f = mov[mov["chave"].isin(chaves_f)]
    qtd_dia_f = qtd_dia_all[chaves_f]
    net_dia_f = net_dia_all[chaves_f]
    unit_f = base_prod.loc[chaves_f, "unit"]
    valor_dia_prod_f = valor_dia_all[chaves_f]
    valor_dia_f = valor_dia_prod_f.sum(axis=1)
    dias_p = pd.date_range(p_ini, p_fim, freq="D")
    mov_p = mov_f[(mov_f["dia"] >= p_ini) & (mov_f["dia"] <= p_fim)]

# ---------- Contexto da IA ----------
t_ent = d["valor_entrada"].sum()
t_sai = d["valor_saida"].sum()
resumo = (
    f"Total de produtos: {len(d)}\n"
    f"Valor total do estoque: {brl(d['total'].sum())}\n"
    f"Unidades em estoque: {un(d['qtd'].sum())}\n"
    f"Valor total de ENTRADAS (acumulado na aba bi): {brl(t_ent)}\n"
    f"Valor total de SAÍDAS (acumulado na aba bi): {brl(t_sai)}\n"
    f"Soma do valor de entradas + valor de saídas: {brl(t_ent + t_sai)}\n"
    f"Por setor (estoque / entradas / saídas, em R$):\n"
)
por_setor_ia = d.groupby("setor")[["total", "valor_entrada", "valor_saida"]].sum()
for setor, linha in por_setor_ia.sort_values("total", ascending=False).iterrows():
    resumo += (f"- {setor}: estoque {brl(linha['total'])} | "
               f"entradas {brl(linha['valor_entrada'])} | saídas {brl(linha['valor_saida'])}\n")
resumo += "Soma de cada coluna numérica da tabela:\n"
for c in colunas_numericas:
    resumo += f"- {c}: {tudo[c].sum():,.2f}\n"

bloco_lanc = (contexto_movimentos(mov, qtd_dia_all, valor_dia_all, base_prod, HOJE, p_ini, p_fim, dia_sel)
              if mov is not None else "")

bloco_hist = ""
if not historico.empty:
    h_txt = historico.tail(45).copy()
    h_txt["data"] = h_txt["data"].dt.strftime("%d/%m/%Y")
    bloco_hist = ("\nHISTÓRICO DIÁRIO (um registro por dia: valor do estoque, valor acumulado de "
                  "entradas e de saídas naquele dia, unidades e produtos):\n"
                  + h_txt.to_csv(index=False))

instrucoes = f"""Você é o assistente de estoque da {NOME_EMPRESA}.
Responda em português, em poucas linhas, usando SOMENTE os dados abaixo.
Se um produto não estiver na tabela, diga que não encontrou. Não invente valores.

A tabela abaixo contém TODAS as colunas da aba de estoque: {", ".join(tudo.columns)}.
Significado das principais: Quantidade = quantidade em estoque; Valor Unitario = valor de
cada unidade (R$); Total = valor em estoque (R$); Saida/Entrada = unidades que saíram/entraram;
Valor de Saida/Valor de Entrada = valores dessas movimentações (R$).

Para totais gerais, por setor, entradas, saídas ou a soma delas, use o RESUMO (já calculado).
Para um produto específico, leia a linha dele na tabela.
Se perguntarem "entrada e saída", informe os dois valores separados e a soma.
Se um valor de saída estiver negativo, informe como está e avise o sinal.
Para perguntas com datas, use os blocos de movimentação quando existirem:
- "PERÍODO ESCOLHIDO" e "DIA ESCOLHIDO" vêm do menu lateral do usuário: use-os quando ele falar
  "no período" ou "no dia escolhido/selecionado".
- Para "ontem", "últimos 7 dias", "semana passada", "mês atual", use "OUTROS PERÍODOS".
- Para o valor do estoque em um dia específico dos últimos 31 dias, use a tabela "POR DIA".
  Se a data pedida não estiver nos blocos, diga que não está nos dados carregados e peça para
  escolher o período ou o dia em "Consulta por data", no menu lateral.
- "entraram" = unidades que entraram (entradas lançadas na planilha); "saíram" = unidades que saíram
  (soma de TODOS os formulários de saída, por exemplo elétrica e mecânica; quando um formulário
  não aparece em "Saídas por formulário", não houve saída dele naquele período). Informe sempre as unidades e, quando pedirem, também os valores em R$.
Não calcule datas nem some períodos por conta própria: use as datas e totais informados.

RESUMO:
{resumo}
ESTOQUE ATUAL (CSV, todas as colunas):
{tudo.to_csv(index=False)}{bloco_lanc}{bloco_hist}
"""

# ---------- Cabeçalho ----------
col_a, col_b = st.columns([1, 4], vertical_alignment="center")
with col_a:
    if TEM_LOGO:
        mostrar_logo(220)
with col_b:
    st.title("Painel de Estoque")
    st.caption("Indicadores em tempo real e assistente com IA.")

aba_painel, aba_mov, aba_chat = st.tabs(["📊 Painel", "📈 Movimentação", "💬 Assistente"])

# ================= PAINEL =================
with aba_painel:
    zerados = df_f[df_f["qtd"] <= 0]
    baixos = df_f[(df_f["qtd"] > 0) & (df_f["qtd"] <= limite)]

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Valor do estoque", brl(df_f["total"].sum()))
    k2.metric("Produtos", f"{len(df_f)}")
    k3.metric("Unidades", un(df_f["qtd"].sum()))
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
            st.altair_chart(rosca, width="stretch")
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
        st.altair_chart(barras, width="stretch")

    st.subheader("Entradas e saídas ao longo do tempo")
    if mov is None:
        st.info("Para ver os gráficos de linhas por dia, publique a aba **lancamentos** (entradas) e as "
                "respostas do formulário de **saída** como CSV e cadastre os links **URL_LANCAMENTOS** e "
                "**URL_SAIDAS_...** (um por formulário) nos Secrets.")
    else:
        st.caption(f"Período: {dmy(p_ini)} a {dmy(p_fim)}. Altere em \"Consulta por data\", na barra lateral.")
        sv = serie_diaria(mov_p, dias_p, "valor")
        sv.index.name = "dia"
        lv = sv.reset_index().melt(id_vars="dia", var_name="tipo", value_name="v")
        l1, l2 = st.columns(2)
        with l1:
            st.markdown("**Valor de entradas e de saídas por dia (R$)**")
            if lv["v"].sum() == 0:
                st.info("Sem movimentações neste período.")
            else:
                st.altair_chart(alt.Chart(lv).mark_line(point=True).encode(
                    x=alt.X("dia:T", title="Dia"),
                    y=alt.Y("v:Q", title="Valor (R$)"),
                    color=alt.Color("tipo:N", title=None, scale=ESCALA),
                    tooltip=[alt.Tooltip("dia:T", title="Dia", format="%d/%m/%Y"),
                             alt.Tooltip("tipo:N", title="Tipo"),
                             alt.Tooltip("v:Q", title="Valor (R$)", format=",.2f")],
                ).properties(height=320), width="stretch")
        with l2:
            st.markdown("**Valor do estoque ao fim de cada dia (R$)**")
            vd = valor_dia_f.loc[p_ini:p_fim].reset_index()
            vd.columns = ["dia", "valor"]
            st.altair_chart(alt.Chart(vd).mark_line(point=True, color=VERDE).encode(
                x=alt.X("dia:T", title="Dia"),
                y=alt.Y("valor:Q", title="Valor (R$)", scale=alt.Scale(zero=False)),
                tooltip=[alt.Tooltip("dia:T", title="Dia", format="%d/%m/%Y"),
                         alt.Tooltip("valor:Q", title="Estoque (R$)", format=",.2f")],
            ).properties(height=320), width="stretch")
            st.caption("Dias passados são reconstruídos a partir da quantidade atual, desfazendo as movimentações posteriores.")

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
        st.altair_chart(crit, width="stretch")

    with st.expander("Ver estoque detalhado (todas as colunas da aba bi)"):
        st.caption(f"{len(tudo.columns)} colunas lidas: " + ", ".join(tudo.columns))
        st.dataframe(tudo.loc[df_f.index], hide_index=True, width="stretch")

# ================= MOVIMENTAÇÃO =================
ESCALA = alt.Scale(domain=["Entrada", "Saída"], range=[VERDE, AZUL])

with aba_mov:
    # ---------- Parte 1: por dia (entradas lançadas + saídas do formulário) ----------
    if mov is not None:
        st.subheader("Movimentação por dia")
        st.caption(f"Movimentações de {dmy(mov['dia'].min())} a {dmy(mov['dia'].max())}. O período e o dia são "
                   "escolhidos em \"Consulta por data\", na barra lateral. Valores usam o valor unitário atual.")

        ent_p = mov_p[mov_p["tipo"] == "Entrada"]
        sai_p = mov_p[mov_p["tipo"] == "Saída"]
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Entradas no período", brl(ent_p["valor"].sum()), f"{un(ent_p['qtd'].sum())} un",
                  delta_color="off")
        a2.metric("Saídas no período", brl(sai_p["valor"].sum()), f"{un(sai_p['qtd'].sum())} un",
                  delta_color="off")
        a3.metric("Saldo (entradas − saídas)", brl(ent_p["valor"].sum() - sai_p["valor"].sum()))
        a4.metric("Registros", f"{len(mov_p)}")

        origens_saida = sorted(mov.loc[mov["tipo"] == "Saída", "origem"].unique())
        if len(origens_saida) > 1:
            st.markdown("**Saídas por formulário (no período)**")
            cols_o = st.columns(len(origens_saida) + 1)
            for col_, o_ in zip(cols_o, origens_saida):
                mo_ = sai_p[sai_p["origem"] == o_]
                col_.metric(o_, brl(mo_["valor"].sum()), f"{un(mo_['qtd'].sum())} un", delta_color="off")
            cols_o[-1].metric("Total das saídas", brl(sai_p["valor"].sum()),
                              f"{un(sai_p['qtd'].sum())} un", delta_color="off")

        medida = st.radio("Medir em", ["Valor (R$)", "Unidades"], horizontal=True)
        coluna = "valor" if medida.startswith("Valor") else "qtd"
        eixo = "Valor (R$)" if coluna == "valor" else "Unidades"

        s = serie_diaria(mov_p, dias_p, coluna)
        s.index.name = "dia"
        longo = s.reset_index().melt(id_vars="dia", var_name="tipo", value_name="v")
        longo["dia_txt"] = longo["dia"].dt.strftime("%d/%m")
        ordem_dias = [x.strftime("%d/%m") for x in dias_p]

        st.markdown("**Entradas x saídas por dia**")
        if longo["v"].sum() == 0:
            st.info("Sem movimentações neste período.")
        else:
            if len(dias_p) <= 31:
                graf_dia = alt.Chart(longo).mark_bar().encode(
                    x=alt.X("dia_txt:N", sort=ordem_dias, title="Dia"),
                    xOffset="tipo:N",
                    y=alt.Y("v:Q", title=eixo),
                    color=alt.Color("tipo:N", title=None, scale=ESCALA),
                    tooltip=[alt.Tooltip("dia:T", title="Dia", format="%d/%m/%Y"),
                             alt.Tooltip("tipo:N", title="Tipo"),
                             alt.Tooltip("v:Q", title=eixo, format=",.2f")],
                ).properties(height=320)
            else:
                graf_dia = alt.Chart(longo).mark_line(point=True).encode(
                    x=alt.X("dia:T", title="Dia"),
                    y=alt.Y("v:Q", title=eixo),
                    color=alt.Color("tipo:N", title=None, scale=ESCALA),
                    tooltip=[alt.Tooltip("dia:T", title="Dia", format="%d/%m/%Y"),
                             alt.Tooltip("tipo:N", title="Tipo"),
                             alt.Tooltip("v:Q", title=eixo, format=",.2f")],
                ).properties(height=320)
            st.altair_chart(graf_dia, width="stretch")

        # ----- Como estávamos em um dia -----
        st.markdown(f"**Como estávamos em {dmy(dia_sel)}**")
        qtd_fim = qtd_dia_f.loc[dia_sel]
        valor_fim = float(valor_dia_f.loc[dia_sel])
        valor_ant = valor_fim - float((net_dia_f.loc[dia_sel] * unit_f).sum())  # valor ao fim do dia anterior
        m_dia = mov_f[mov_f["dia"] == dia_sel]
        e_dia = m_dia.loc[m_dia["tipo"] == "Entrada", "valor"].sum()
        s_dia = m_dia.loc[m_dia["tipo"] == "Saída", "valor"].sum()
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Valor do estoque ao fim do dia", brl(valor_fim), delta_brl(valor_fim - valor_ant))
        r2.metric("Entradas do dia", brl(e_dia))
        r3.metric("Saídas do dia", brl(s_dia))
        r4.metric("Unidades em estoque", un(qtd_fim.sum()))
        st.caption(f"Variação em relação ao fim de {dmy(dia_sel - pd.Timedelta(days=1))}.")

        so_mov = st.checkbox("Mostrar só os produtos que tiveram movimento nesse dia", value=True)
        tab = pd.DataFrame({
            "Produto": base_prod.loc[chaves_f, "produto"].values,
            "Setor": base_prod.loc[chaves_f, "setor"].values,
            "Qtd ao fim do dia": qtd_fim.values,
            "Valor (R$)": valor_dia_prod_f.loc[dia_sel].values,
        }, index=chaves_f.values)
        tab["Entraram (un)"] = m_dia[m_dia["tipo"] == "Entrada"].groupby("chave")["qtd"].sum()
        tab["Saíram (un)"] = m_dia[m_dia["tipo"] == "Saída"].groupby("chave")["qtd"].sum()
        tab[["Entraram (un)", "Saíram (un)"]] = tab[["Entraram (un)", "Saíram (un)"]].fillna(0.0)
        if so_mov:
            tab = tab[(tab["Entraram (un)"] > 0) | (tab["Saíram (un)"] > 0)]
        st.dataframe(tab.sort_values("Produto"), hide_index=True, width="stretch")

        # ----- Produtos mais movimentados no período -----
        st.markdown("**Produtos mais movimentados no período**")
        if mov_p.empty:
            st.info("Sem movimentações neste período.")
        else:
            gp = mov_p.pivot_table(index="produto", columns="tipo", values=coluna,
                                   aggfunc="sum").fillna(0.0)
            for t_ in ("Entrada", "Saída"):
                if t_ not in gp.columns:
                    gp[t_] = 0.0
            gp["tot"] = gp["Entrada"] + gp["Saída"]
            gp = gp.sort_values("tot", ascending=False).head(10)
            ordem = gp.index.tolist()
            gpl = gp[["Entrada", "Saída"]].reset_index().melt(
                id_vars="produto", var_name="tipo", value_name="v")
            graf_top = alt.Chart(gpl).mark_bar().encode(
                y=alt.Y("produto:N", sort=ordem, title=None),
                yOffset="tipo:N",
                x=alt.X("v:Q", title=eixo),
                color=alt.Color("tipo:N", title=None, scale=ESCALA),
                tooltip=[alt.Tooltip("produto:N", title="Produto"),
                         alt.Tooltip("tipo:N", title="Tipo"),
                         alt.Tooltip("v:Q", title=eixo, format=",.2f")],
            ).properties(height=380)
            st.altair_chart(graf_top, width="stretch")

        with st.expander("Últimos registros"):
            ult = mov_f.sort_values("data", ascending=False).head(40)
            st.dataframe(pd.DataFrame({
                "Data": ult["data"].dt.strftime("%d/%m/%Y %H:%M"),
                "Produto": ult["produto"], "Tipo": ult["tipo"],
                "Quantidade": ult["qtd"], "Origem": ult["origem"],
            }), hide_index=True, width="stretch")

    # ---------- Se não há lançamentos: histórico diário ou coluna de data da bi ----------
    elif not historico.empty:
        st.subheader("Evolução por dia")
        st.caption("Retrato diário gravado pelo script da planilha (um registro por dia).")
        variacao = st.checkbox("Mostrar a variação diária (diferença entre um dia e o anterior)")
        h = historico.copy()
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Valor do estoque por dia**")
            st.altair_chart(alt.Chart(h).mark_line(point=True, color=VERDE).encode(
                x=alt.X("data:T", title="Dia"),
                y=alt.Y("estoque:Q", title="Valor (R$)", scale=alt.Scale(zero=False)),
                tooltip=[alt.Tooltip("data:T", title="Dia", format="%d/%m/%Y"),
                         alt.Tooltip("estoque:Q", title="Estoque (R$)", format=",.2f")],
            ).properties(height=300), width="stretch")
        with c2:
            st.markdown("**Entradas x saídas por dia**")
            s_h = h[["data", "entradas", "saidas"]].copy()
            if variacao:
                s_h["entradas"] = s_h["entradas"].diff()
                s_h["saidas"] = s_h["saidas"].diff()
                s_h = s_h.dropna()
            s_h["saidas"] = s_h["saidas"].abs()
            sl = s_h.melt(id_vars="data", value_vars=["entradas", "saidas"],
                          var_name="tipo", value_name="valor")
            sl["tipo"] = sl["tipo"].map({"entradas": "Entrada", "saidas": "Saída"})
            if sl.empty:
                st.info("Ainda não há dias suficientes para calcular a variação.")
            else:
                st.altair_chart(alt.Chart(sl).mark_line(point=True).encode(
                    x=alt.X("data:T", title="Dia"),
                    y=alt.Y("valor:Q", title="Valor (R$)"),
                    color=alt.Color("tipo:N", title=None, scale=ESCALA),
                    tooltip=[alt.Tooltip("data:T", title="Dia", format="%d/%m/%Y"),
                             alt.Tooltip("tipo:N", title="Tipo"),
                             alt.Tooltip("valor:Q", title="Valor (R$)", format=",.2f")],
                ).properties(height=300), width="stretch")

    elif "data" in df_f.columns and df_f["data"].notna().any():
        st.subheader("Evolução por dia")
        st.caption("Movimentações agrupadas pela coluna de data da aba bi.")
        por_dia = df_f.dropna(subset=["data"]).groupby("data", as_index=False)[
            ["valor_entrada", "valor_saida"]].sum()
        por_dia["valor_saida"] = por_dia["valor_saida"].abs()
        dl = por_dia.melt(id_vars="data", value_vars=["valor_entrada", "valor_saida"],
                          var_name="tipo", value_name="valor")
        dl["tipo"] = dl["tipo"].map({"valor_entrada": "Entrada", "valor_saida": "Saída"})
        st.altair_chart(alt.Chart(dl).mark_line(point=True).encode(
            x=alt.X("data:T", title="Dia"),
            y=alt.Y("valor:Q", title="Valor (R$)"),
            color=alt.Color("tipo:N", title=None, scale=ESCALA),
            tooltip=[alt.Tooltip("data:T", title="Dia", format="%d/%m/%Y"),
                     alt.Tooltip("tipo:N", title="Tipo"),
                     alt.Tooltip("valor:Q", title="Valor (R$)", format=",.2f")],
        ).properties(height=320), width="stretch")

    else:
        st.subheader("Movimentação por dia")
        if not FONTES:
            st.info("Para ver entradas, saídas e o estoque dia a dia, publique como CSV a aba "
                    "**lancamentos** (entradas) e a aba de respostas do formulário de **saída**, e cadastre "
                    "os links **URL_LANCAMENTOS** e **URL_SAIDAS_ELETRICA**, **URL_SAIDAS_MECANICA** nos Secrets.")
        elif not erros_fontes:
            st.info("As fontes foram lidas, mas ainda não têm registros válidos.")

    if avisos_lanc or diags or erros_fontes or AVISO_TOTAL:
        with st.expander(f"⚠️ Conferência das fontes de dados ({len(avisos_lanc) + len(erros_fontes) + (1 if AVISO_TOTAL else 0)} aviso(s))"):
            if AVISO_TOTAL:
                st.warning(AVISO_TOTAL)
            for e_ in erros_fontes:
                st.error(e_)
            for a_ in avisos_lanc:
                st.write("- " + a_)
            for dg in diags:
                st.markdown(f"**{dg['origem']}** — {dg['linhas']} linha(s) lida(s)")
                st.caption(f"Colunas usadas -> data: {dg['data']} | produto: {dg['produto']} | "
                           f"quantidade: {dg['quantidade']}")
                st.caption("Todas as colunas: " + ", ".join(map(str, dg["colunas"])))
            if mov is not None:
                ent_mov = mov.loc[mov["tipo"] == "Entrada", "qtd"].sum()
                sai_mov = mov.loc[mov["tipo"] == "Saída", "qtd"].sum()
                st.write(f"Entradas: {un(d['entrada'].sum())} un na coluna Entrada da aba bi | "
                         f"{un(ent_mov)} un nos lançamentos com data.")
                st.write(f"Saídas: {un(abs(d['saida'].sum()))} un na coluna Saida da aba bi | "
                         f"{un(sai_mov)} un nos formulários de saída (soma de todos).")
                st.caption("Se os números forem muito diferentes, a aba bi e as fontes com data não estão "
                           "contando as mesmas movimentações, e o estoque de dias passados fica menos preciso. "
                           "Registros ignorados (linhas indicadas acima) não entram nos gráficos nem na IA.")

    # ---------- Parte 2: acumulado da aba bi por setor ----------
    st.divider()
    st.subheader("Acumulado na aba bi: saída x entrada por setor")
    usar_abs = st.checkbox("Comparar saídas em valor absoluto (ignora o sinal negativo)", value=True)
    base = df_f.copy()
    if usar_abs:
        base["valor_saida"] = base["valor_saida"].abs()
    v_ent = base["valor_entrada"].sum()
    v_sai = base["valor_saida"].sum()
    m1, m2, m3 = st.columns(3)
    m1.metric("Valor de entradas", brl(v_ent))
    m2.metric("Valor de saídas", brl(v_sai))
    m3.metric("Saldo (entradas − saídas)", brl(v_ent - v_sai))

    mv = base.groupby("setor", as_index=False)[["valor_entrada", "valor_saida"]].sum()
    mv = mv.melt(id_vars="setor", value_vars=["valor_entrada", "valor_saida"],
                 var_name="tipo", value_name="valor")
    mv["tipo"] = mv["tipo"].map({"valor_entrada": "Entrada", "valor_saida": "Saída"})
    if mv["valor"].abs().sum() > 0:
        st.altair_chart(alt.Chart(mv).mark_bar().encode(
            x=alt.X("setor:N", title=None),
            xOffset="tipo:N",
            y=alt.Y("valor:Q", title="Valor (R$)"),
            color=alt.Color("tipo:N", title=None, scale=ESCALA),
            tooltip=[alt.Tooltip("setor:N", title="Setor"),
                     alt.Tooltip("tipo:N", title="Tipo"),
                     alt.Tooltip("valor:Q", title="Valor (R$)", format=",.2f")],
        ).properties(height=300), width="stretch")
    else:
        st.info("Sem movimentação para mostrar.")

# ================= ASSISTENTE =================
with aba_chat:
    if "lista_mensagens" not in st.session_state:
        st.session_state["lista_mensagens"] = []

    AVATAR_IA = ICONE if TEM_ICONE else "🤖"

    for m_ in st.session_state["lista_mensagens"]:
        av = AVATAR_IA if m_["role"] == "assistant" else None
        st.chat_message(m_["role"], avatar=av).write(m_["content"])

    pergunta_rapida = None
    if not st.session_state["lista_mensagens"]:
        st.write("**Experimente perguntar:**")
        c1, c2, c3, c4 = st.columns(4)
        if c1.button("Valor total do estoque", width="stretch"):
            pergunta_rapida = "Qual o valor total do estoque?"
        if c2.button("Estoque no dia escolhido", width="stretch"):
            pergunta_rapida = "Qual o valor do estoque no dia escolhido e como ele variou em relação ao dia anterior?"
        if c3.button("Entradas e saídas do período", width="stretch"):
            pergunta_rapida = ("Quanto entrou e quanto saiu no período escolhido? "
                               "Mostre as quantidades por produto.")
        if c4.button("Estoque por setor", width="stretch"):
            pergunta_rapida = "Qual o valor de estoque, entradas e saídas de cada setor?"

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
                    + [{"role": m_["role"], "content": m_["content"].replace("\\$", "$")}
                       for m_ in st.session_state["lista_mensagens"]])
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
