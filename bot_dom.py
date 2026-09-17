"""
Bot DOM Salvador -> Google Chat  (versão 2)

O que faz:
  1. Encontra a edição mais recente do DOM de Salvador.
  2. Lê o PDF respeitando as colunas da página.
  3. Separa o texto em atos (extratos, avisos, aditivos, atas...).
  4. Mantém só os atos de tecnologia (de qualquer órgão) e os atos
     contratuais/licitatórios da SEMIT/SMART.
  5. Publica no Google Chat, organizado por tópicos.

Variáveis de ambiente:
  GCHAT_WEBHOOK_URL   (obrigatória) URL do webhook do espaço
  ANTHROPIC_API_KEY   (opcional) para resumo com IA
  FORCAR_ENVIO        (opcional) "sim" reenvia a edição mesmo que já tenha sido enviada
"""
import os, re, io
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
import pdfplumber

DOM_HOME = "http://www.dom.salvador.ba.gov.br/"
STATE_FILE = "ultima_edicao.txt"
HEADERS = {"User-Agent": "Mozilla/5.0 (bot-dom-smart)"}

# ---------------------------------------------------------------------------
# CONFIGURAÇÕES QUE VOCÊ PODE AJUSTAR
# ---------------------------------------------------------------------------

# Siglas cujos contratos/licitações entram sempre, mesmo sem termo de TIC
ORGAOS_SEMPRE = ["SEMIT", "SMART"]

# Termos que indicam tecnologia
TERMOS_TIC = [
    r"tecnologia da informa", r"\bTIC\b", r"software", r"\bSaaS\b", r"licen[çc]as? de uso",
    r"licenciamento de (software|uso|licen)", r"nuvem", r"\bcloud\b", r"data ?center",
    r"desenvolvimento de sistemas?", r"sistemas? (de informa|informatizad|web)", r"sistema de gerenciamento",
    r"link(s)? de (internet|dados|comunica)", r"\binternet\b", r"fibra [óo]ptica", r"redes? de dados",
    r"computador", r"notebook", r"microcomputador", r"servidor(es)? de (rede|dados|aplica)",
    r"ciberseguran", r"seguran[çc]a da informa", r"\bLGPD\b", r"telecomunica", r"telefonia",
    r"outsourcing de impress", r"impressoras?", r"transforma[çc][ãa]o digital", r"intelig[êe]ncia artificial",
    r"\bERP\b", r"digitaliza[çc][ãa]o", r"\bGED\b", r"\bOCR\b", r"banco de dados", r"\bbackup\b",
    r"firewall", r"antiv[íi]rus", r"manuten[çc][ãa]o evolutiva", r"sustenta[çc][ãa]o de sistemas?",
    r"registro eletr[ôo]nico de ponto", r"videomonitoramento", r"aplicativo", r"plataforma digital",
    r"hospedagem", r"certificados? digita", r"wi-?fi", r"\bMicrosoft\b", r"\bOracle\b", r"\bGoogle\b",
    r"\bAWS\b", r"\bSAP\b", r"\bGartner\b", r"inform[áa]tica", r"tablets?", r"switch(es)?",
]

# Expressões que parecem tecnologia mas não são (removidas antes da busca)
FALSOS_POSITIVOS = [
    r"secretaria municipal de inova[çc][ãa]o e tecnologia", r"sistema de gest[ãa]o de materiais",
    r"\bSIGM\b", r"sistema [úu]nico de sa[úu]de", r"sistema municipal de \w+", r"sistema vi[áa]rio",
    r"licenciamento ambiental", r"licen[çc]a ambiental", r"rede municipal de ensino",
    r"rede de (aten[çc][ãa]o|sa[úu]de|ensino)", r"sistema de registro de pre[çc]os?",
    r"sistema eletr[ôo]nico de (informa[çc][õo]es|compras|licita)", r"compras\.gov",
    r"sess[ãa]o p[úu]blica (eletr[ôo]nica|virtual)", r"portal de compras",
]

# Atos descartados mesmo que citem tecnologia
DESCARTAR = [
    r"suplementa[çc][ãa]o", r"cr[ée]dito suplementar", r"\bnomear\b", r"\bexonerar\b",
    r"\bf[ée]rias\b", r"\baposentadoria\b", r"licen[çc]a[- ]pr[êe]mio",
]

# Tópicos da mensagem: (chave, título, regex do cabeçalho do ato)
TOPICOS = [
    ("contrato", "📝 *Contratos, aditivos e apostilamentos*",
     r"(EXTRATO|RESUMO)\s+(D[OAE]S?\s+)?(CONTRATO|TERMO\s+ADITIVO|ADITIVO|APOSTILA|CONV[ÊE]NIO|ACORDO|TERMO\s+DE\s+(COLABORA|FOMENTO|COOPERA))|TERMO\s+ADITIVO|APOSTILAMENTO|TERMO\s+DE\s+APOSTILA"),
    ("resultado", "✅ *Resultados, homologações e atas*",
     r"(AVISO\s+DE\s+|TERMO\s+DE\s+|EXTRATO\s+D[AE]\s+)?(HOMOLOGA|ADJUDICA|RATIFICA|RESULTADO)|(EXTRATO\s+D[AE]\s+)?ATA\s+DE\s+REGISTRO\s+DE\s+PRE"),
    ("licitacao", "📢 *Licitações, cotações e editais*",
     r"AVISO\s+DE\s+(LICITA|PREG|CONCORR|COTA|DISPENSA|CHAMAMENTO|INTEN|SESS|REABERTURA|ADIAMENTO|SUSPENS|RETIFICA|REVOGA|ANULA|CREDENCIA)|AVISO\s+DE\s+CONTRATA|EDITAL|CHAMAMENTO\s+P[ÚU]BLICO|INTEN[ÇC][ÃA]O\s+DE\s+REGISTRO|DISPENSA\s+(DE\s+LICITA|ELETR)|INEXIGIBILIDADE"),
    ("outros", "📌 *Outros atos*",
     r"PORTARIA\s+N|DECRETO\s+N|RESOLU[ÇC][ÃA]O\s+N|INSTRU[ÇC][ÃA]O\s+NORMATIVA|EXTRATO\b|AVISO\b|DESPACHO"),
]

# ---------------------------------------------------------------------------

RE_TIC = re.compile("|".join(TERMOS_TIC), re.I)
RE_FALSO = re.compile("|".join(FALSOS_POSITIVOS), re.I)
RE_DESCARTAR = re.compile("|".join(DESCARTAR), re.I)
RE_SEMPRE = re.compile(r"\b(" + "|".join(ORGAOS_SEMPRE) + r")\b")
TOPICOS_RE = [(k, t, re.compile(r"^\s*(" + r + ")")) for k, t, r in TOPICOS]

RE_ORGAO = re.compile(
    r"^(SECRETARIA|SUPERINTEND[ÊE]NCIA|CASA CIVIL|CONTROLADORIA|PROCURADORIA|FUNDA[ÇC][ÃA]O|EMPRESA|"
    r"COMPANHIA|AG[ÊE]NCIA|DIRETORIA|GUARDA CIVIL|OUVIDORIA|GABINETE DO PREFEITO|GABINETE DO VICE|"
    r"TRANSALVADOR|LIMPURB|DESAL|COGEL|SALTUR|FUNDA[ÇC][ÃA]O GREGÓRIO)[A-ZÀ-Ú ,\-–/]*$"
)
RE_CABECALHO = [
    re.compile(r"^DI[ÁA]RIO OFICIAL DO\b.*$"), re.compile(r"^SALVADOR-BAHIA\b.*$"),
    re.compile(r"^(SEGUNDA|TER[ÇC]A|QUARTA|QUINTA|SEXTA|S[ÁA]BADO|DOMINGO)[- A-ZÀ-Ú]*\d{1,2} (A \d{1,2} )?DE [A-ZÇ]+ DE \d{4}.*$"),
    re.compile(r"^ANO [IVXLC]+ ?\| ?N ?º.*$"), re.compile(r"^\d{1,3}$"),
    re.compile(r".*\bDE \d{4} ANO [IVXLC]+ ?\| ?N ?º.*$"),  # pedaços do cabeçalho cortados pela coluna
    re.compile(r"^.{0,40}ANO [IVXLC]+ ?\| ?N ?º ?[\d.]+$"),
]


# ------------------------------- COLETA ------------------------------------

def achar_pdf_do_dia():
    """Procura na página inicial do DOM o link do PDF da edição mais recente."""
    html = requests.get(DOM_HOME, headers=HEADERS, timeout=60).text
    soup = BeautifulSoup(html, "html.parser")
    links = [urljoin(DOM_HOME, a["href"]) for a in soup.find_all("a", href=True)
             if ".pdf" in a["href"].lower()]
    # Prefere links no padrão dom-NNNN-DD-MM-AAAA.pdf, pelo maior número de edição
    padrao = [l for l in links if re.search(r"dom-\d+-\d{2}-\d{2}-\d{4}", l, re.I)]
    if padrao:
        return max(padrao, key=lambda l: int(re.search(r"dom-(\d+)", l, re.I).group(1)))
    return links[0] if links else None


def dados_da_edicao(url_pdf):
    m = re.search(r"dom-(\d+)-(\d{2})-(\d{2})-(\d{4})", url_pdf, re.I)
    if m:
        return f"Edição nº {int(m.group(1)):,}".replace(",", "."), f"{m.group(2)}/{m.group(3)}/{m.group(4)}"
    return "Edição", ""


# -------------------------- LEITURA POR COLUNAS ----------------------------

def colunas_da_pagina(page):
    """Descobre as colunas procurando faixas verticais sem texto."""
    w, h = page.width, page.height
    palavras = [p for p in page.extract_words() if h * 0.10 < p["top"] < h * 0.92]
    if len(palavras) < 30:
        return [(0, w)]
    ocupado = [0] * (int(w) + 1)
    for p in palavras:
        for x in range(int(p["x0"]), min(int(p["x1"]) + 1, int(w))):
            ocupado[x] += 1
    limite = max(1, len(palavras) * 0.01)  # tolera poucas palavras (tabelas)
    cortes, x = [], int(w * 0.15)
    while x < int(w * 0.85):
        if ocupado[x] <= limite:
            ini = x
            while x < int(w * 0.85) and ocupado[x] <= limite:
                x += 1
            if x - ini >= 6:
                cortes.append((ini + x) / 2)
        x += 1
    bordas = [0] + cortes + [w]
    return [(bordas[i], bordas[i + 1]) for i in range(len(bordas) - 1)]


def ler_pdf(conteudo):
    """Retorna lista de (nº da página, linha) na ordem de leitura."""
    linhas = []
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        for n, page in enumerate(pdf.pages, start=1):
            for x0, x1 in colunas_da_pagina(page):
                txt = page.crop((x0, 0, x1, page.height)).extract_text() or ""
                for l in txt.split("\n"):
                    l = l.strip()
                    if l and not any(r.match(l) for r in RE_CABECALHO):
                        linhas.append((n, l))
    return linhas


# ---------------------------- SEPARAR ATOS ---------------------------------

def maiusculo(l):
    letras = [c for c in l if c.isalpha()]
    return len(letras) >= 4 and sum(c.isupper() for c in letras) / len(letras) > 0.85


def separar_atos(linhas):
    atos, atual, orgao = [], None, ""
    i = 0
    while i < len(linhas):
        pag, l = linhas[i]
        # Cabeçalho de órgão (pode quebrar a sigla na linha seguinte)
        if maiusculo(l) and len(l) < 130 and not re.search(r"\d", l) and RE_ORGAO.match(l):
            nome = l
            if i + 1 < len(linhas) and re.fullmatch(r"[-–]?\s*[A-Z]{2,12}", linhas[i + 1][1]) \
                    and (nome.endswith(("-", "–")) or linhas[i + 1][1].startswith(("-", "–"))):
                nome = nome.rstrip(" -–") + " - " + linhas[i + 1][1].lstrip(" -–")
                i += 1
            orgao = re.sub(r"\s+", " ", nome)
            i += 1
            continue
        # Início de um ato
        if maiusculo(l[:60]):
            for chave, _, rx in TOPICOS_RE:
                if rx.match(l):
                    titulo = l
                    # título quebrado em duas linhas (ex.: "... CONTRATO Nº" + "632-D/2022")
                    if i + 1 < len(linhas) and (re.search(r"(N[º°o]\.?|AO|DO|DA|DE|-)$", l) or re.fullmatch(r"[\d./A-Z-]{3,20}", linhas[i + 1][1])):
                        titulo += " " + linhas[i + 1][1]
                        i += 1
                    atual = {"topico": chave, "titulo": titulo, "orgao": orgao, "pagina": pag, "linhas": []}
                    atos.append(atual)
                    break
            else:
                if atual:
                    atual["linhas"].append(l)
        elif atual:
            atual["linhas"].append(l)
        i += 1
    for a in atos:
        a["texto"] = re.sub(r"\s+", " ", " ".join(a["linhas"])).strip()
        a["sigla"] = (re.search(r"-\s*([A-Z]{2,12})$", a["orgao"]) or [None, a["orgao"][:40]])[1]
    return atos


def eh_relevante(ato):
    completo = ato["titulo"] + " " + ato["texto"]
    if RE_DESCARTAR.search(completo):
        return False
    if ato["topico"] != "outros" and (RE_SEMPRE.search(ato["orgao"]) or RE_SEMPRE.search(completo)):
        return True
    return bool(RE_TIC.search(RE_FALSO.sub(" ", completo)))


# ------------------------------ RESUMO -------------------------------------

def campo(rx, texto, limite=220):
    m = re.search(rx, texto, re.I)
    if not m:
        return ""
    v = m.group(m.lastindex or 0).strip(" :.-;,")
    return v if len(v) <= limite else v[:limite].rsplit(" ", 1)[0] + "…"


SIGLAS_TITULO = {"SEMIT", "SMART", "SEMOB", "SEMGE", "SECIS", "SMED", "SMS", "SEFAZ", "SEMOP", "SEINFRA",
                 "SEDUR", "SEMPRE", "SEGOV", "SECULT", "SEMAN", "TRANSALVADOR", "LIMPURB", "PGMS", "CGM",
                 "TIC", "SRP", "ARP", "CNPJ"}


def titulo_legivel(t):
    """'RESUMO DO TERMO ADITIVO Nº 051/2024' -> 'Resumo do termo aditivo nº 051/2024'."""
    pal = []
    for p in t[:100].split():
        limpo = re.sub(r"[^\wÀ-ú]", "", p)
        pal.append(p if re.search(r"\d", p) or limpo in SIGLAS_TITULO else p.lower())
    s = " ".join(pal)
    return s[:1].upper() + s[1:]


def resumir_ato(a):
    t = a["texto"]
    objeto = campo(r"OBJETO\s*(?:DO\s+\w+\s*)?[:\-–]\s*(.+?)(?=\s+(?-i:VALOR|VIG[ÊE]NCIA|PRAZO|CONTRATAD[AO]|DOTA[ÇC][ÃA]O|FUNDAMENTO|AMPARO LEGAL|DATA DA ASSINATURA|PROCESSO)[^:.]{0,15}:|$)", t) \
        or campo(r"(?:tem por (?:objeto|finalidade)|objetivando a?|visando a?)\s+(.+?)(?:[.;]\s|$)", t) \
        or campo(r"(contrata[çc][ãa]o de .+?)(?:[.;]\s|,\s+com a abertura|$)", t)
    empresa = campo(r"(?:CONTRATAD[AO]|EMPRESA|FORNECEDOR|ADJUDICAT[ÁA]RI[AO]|VENCEDOR[A]?)\s*[:\-–]\s*(.+?)(?=,|\s+CNPJ|\s+-\s|\.\s|;|$)", t, 90)
    valor = campo(r"(R\$\s?[\d.]+,\d{2})", t, 30)
    prazo = campo(r"((?:at[ée] o dia|abertura da sess[ãa]o no dia|sess[ãa]o p[úu]blica (?:no dia|em))\s+\d{1,2}(?:/\d{2}/\d{4}| de \w+ de \d{4})(?:,? [àa]s \d{1,2}[:h]\d{0,2}h?)?)", t, 70)

    linha = f"• *{a['sigla']}* – {titulo_legivel(a['titulo'])} _(pág. {a['pagina']})_"
    det = []
    if objeto: det.append(f"Objeto: {objeto}")
    if empresa: det.append(f"Empresa: {empresa}")
    if valor: det.append(f"Valor: {valor}")
    if prazo: det.append(f"Prazo: {prazo}")
    if not det:
        det.append(t[:220].rsplit(" ", 1)[0] + "…")
    return linha + "\n   " + "\n   ".join(det)


ORDEM_EXIBICAO = ["licitacao", "contrato", "resultado", "outros"]


def montar_por_regras(atos):
    blocos = []
    titulos = {k: t for k, t, _ in TOPICOS}
    for chave in ORDEM_EXIBICAO:
        titulo = titulos[chave]
        itens = [resumir_ato(a) for a in atos if a["topico"] == chave]
        if itens:
            blocos.append(titulo + "\n" + "\n\n".join(itens))
    return "\n\n".join(blocos)


def montar_com_ia(atos):
    material = "\n\n".join(
        f"[{i}] TIPO: {a['topico']} | ÓRGÃO: {a['orgao']} | PÁGINA: {a['pagina']}\n{a['titulo']}\n{a['texto'][:3000]}"
        for i, a in enumerate(atos, 1))
    prompt = (
        "Você recebe atos do Diário Oficial do Município de Salvador pré-selecionados por "
        "tratarem de tecnologia. Monte um apanhado para uma equipe de contratações de TIC.\n"
        "Regras:\n"
        "- Descarte atos que não tenham relação real com tecnologia, exceto contratos, "
        "licitações e resultados da SEMIT/SMART, que ficam sempre.\n"
        "- Organize em tópicos, nesta ordem, omitindo tópicos vazios:\n"
        "  📢 *Licitações, cotações e editais*\n  📝 *Contratos, aditivos e apostilamentos*\n"
        "  ✅ *Resultados, homologações e atas*\n  📌 *Outros atos*\n"
        "- Cada item: `• *SIGLA DO ÓRGÃO* – tipo e número do ato _(pág. N)_` e, na linha "
        "de baixo, um resumo de 1 a 2 linhas com objeto, empresa, valor e prazos/datas "
        "quando houver.\n"
        "- Formatação do Google Chat: *negrito*, _itálico_. Sem títulos markdown (#), sem tabelas.\n"
        "- Não invente dados. Se nada for relevante, responda apenas: Nenhum ato de TIC identificado.\n\n"
        + material[:150000])
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-5", "max_tokens": 3000,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=240)
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"]).strip()


# ------------------------------- ENVIO -------------------------------------

def enviar_chat(texto):
    """Divide em mensagens de até ~3.900 caracteres, sem cortar itens ao meio."""
    webhook = os.environ["GCHAT_WEBHOOK_URL"]
    partes, atual = [], ""
    for bloco in texto.split("\n\n"):
        if len(atual) + len(bloco) + 2 > 3900 and atual:
            partes.append(atual)
            atual = ""
        atual = (atual + "\n\n" + bloco) if atual else bloco[:3900]
    if atual:
        partes.append(atual)
    for p in partes:
        requests.post(webhook, json={"text": p}, timeout=30).raise_for_status()


def gerar_mensagem(url_pdf, conteudo_pdf):
    edicao, data = dados_da_edicao(url_pdf)
    atos = [a for a in separar_atos(ler_pdf(conteudo_pdf)) if eh_relevante(a)]
    if not atos:
        corpo = "Nenhum ato de TIC identificado nesta edição."
    elif os.environ.get("ANTHROPIC_API_KEY"):
        corpo = montar_com_ia(atos)
    else:
        corpo = montar_por_regras(atos)
    qtd = f" · {len(atos)} ato(s) de TIC" if atos else ""
    return f"📰 *DOM Salvador – {edicao} – {data}*{qtd}\n{url_pdf}\n\n{corpo}"


def main():
    url_pdf = achar_pdf_do_dia()
    if not url_pdf:
        print("PDF não encontrado.")
        return
    ultima = open(STATE_FILE).read().strip() if os.path.exists(STATE_FILE) else ""
    if url_pdf == ultima and os.environ.get("FORCAR_ENVIO") != "sim":
        print("Edição já enviada.")
        return
    conteudo = requests.get(url_pdf, headers=HEADERS, timeout=180).content
    mensagem = gerar_mensagem(url_pdf, conteudo)
    enviar_chat(mensagem)
    with open(STATE_FILE, "w") as f:
        f.write(url_pdf)
    print("Enviado.\n\n" + mensagem)


if __name__ == "__main__":
    main()
