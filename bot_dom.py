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
  OPENAI_API_KEY      (opcional) chave da OpenAI (sk-proj-...) para resumo com IA
  OPENAI_MODEL        (opcional) modelo da OpenAI; padrão: gpt-5-mini
  ANTHROPIC_API_KEY   (opcional) chave da Anthropic (sk-ant-...), alternativa à OpenAI
  FORCAR_ENVIO        (opcional) "sim" reenvia a edição mesmo que já tenha sido enviada
  DIAGNOSTICO         (opcional) "sim" mostra o motivo de cada item e lista todos os atos no log
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
    r"tecnologia da informa", r"\bTIC\b", r"software", r"\bSaaS\b", r"licen[çc]as? de (uso de )?(software|programas?)", r"cess[ãa]o de (direito de )?uso de (software|sistema)",
    r"licenciamento de (software|licen[çc]as)", r"nuvem", r"\bcloud\b", r"data ?center",
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
    r"licen[çc]a de uso de (espa[çc]o|[áa]rea|equipamento urbano)", r"servi[çc]os? reprogr[áa]fic\w*",
    r"(por meio|atrav[ée]s) (da|do) (internet|plataforma|sistema)", r"endere[çc]o eletr[ôo]nico", r"s[íi]tio eletr[ôo]nico",
]

# Atos descartados mesmo que citem tecnologia
DESCARTAR = [
    r"suplementa[çc][ãa]o", r"cr[ée]dito suplementar", r"\bnomear\b", r"\bexonerar\b",
    r"\bf[ée]rias\b", r"\baposentadoria\b", r"licen[çc]a[- ]pr[êe]mio",
]

# Tópicos da mensagem: (chave, título, regex do cabeçalho do ato)
TOPICOS = [
    ("contrato", "📝 *Contratos, aditivos e apostilamentos*",
     r"(EXTRATO|RESUMO)\s+(D[OAE]S?\s+)?(\d+\s*[ºª°o]?\s+|(PRIMEIR|SEGUND|TERCEIR|QUART|QUINT|SEXT|S[ÉE]TIM|OITAV|NON|D[ÉE]CIM)[OA]\s+)?(CONTRATO|TERMO|ADITIVO|APOSTILA|CONV[ÊE]NIO|ACORDO|RESCIS)|(\d+\s*[ºª°o]?\s+|(PRIMEIR|SEGUND|TERCEIR|QUART|QUINT|SEXT|S[ÉE]TIM|OITAV|NON|D[ÉE]CIM)[OA]\s+)?TERMO\s+(ADITIVO|DE\s+(APOSTILA|PRORROGA|RESCIS|RERRATIFICA))|APOSTILAMENTO|RESCIS[ÃA]O\s+(UNILATERAL|AMIG|CONTRATUAL|DO\s+CONTRATO)|RETIFICA[ÇC][ÃA]O\s+(DE|DO|DA)?\s*(RESUMO|EXTRATO|TERMO|CONTRATO)"),
    ("resultado", "✅ *Resultados, homologações e atas*",
     r"(AVISO\s+DE\s+|TERMO\s+DE\s+|EXTRATO\s+D[AE]\s+)?(HOMOLOGA|ADJUDICA|RATIFICA|RESULTADO)|(EXTRATO\s+D[AE]\s+)?ATA\s+DE\s+REGISTRO\s+DE\s+PRE"),
    ("licitacao", "📢 *Licitações, cotações e editais*",
     r"AVISO\s+DE\s+(LICITA|PREG|CONCORR|COTA|DISPENSA|CHAMAMENTO|INTEN|SESS|REABERTURA|ADIAMENTO|SUSPENS|RETIFICA|REVOGA|ANULA|CREDENCIA|CONVOCA)|AVISO\s+DE\s+CONTRATA|EDITAL|CHAMAMENTO\s+P[ÚU]BLICO|INTEN[ÇC][ÃA]O\s+DE\s+REGISTRO|DISPENSA\s+(DE\s+LICITA|ELETR)|INEXIGIBILIDADE"),
    ("outros", "📌 *Outros atos*",
     r"PORTARIA\s+N|DECRETO\s+N|RESOLU[ÇC][ÃA]O\s+N|INSTRU[ÇC][ÃA]O\s+NORMATIVA|EXTRATO\b|AVISO\b|DESPACHO"),
]

# ---------------------------------------------------------------------------

RE_TIC = re.compile("|".join(TERMOS_TIC), re.I)
RE_FALSO = re.compile("|".join(FALSOS_POSITIVOS), re.I)
RE_DESCARTAR = re.compile("|".join(DESCARTAR), re.I)
RE_SEMPRE = re.compile(r"\b(" + "|".join(ORGAOS_SEMPRE) + r")\b")
RE_PROCESSO_SEMPRE = re.compile(r"PROCESSO[^:\d]{0,15}:?\s*[\d./]+\s*[-–/]\s*(" + "|".join(ORGAOS_SEMPRE) + r")\b", re.I)
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
            extras = 0
            while (extras < 3 and i + 1 < len(linhas) and not re.search(r"-\s*[A-Z]{2,12}$", nome)
                   and maiusculo(linhas[i + 1][1]) and len(linhas[i + 1][1]) < 90
                   and not re.search(r"\d", linhas[i + 1][1])
                   and not any(rx.match(linhas[i + 1][1]) for _, _, rx in TOPICOS_RE)):
                nome = nome.rstrip() + " " + linhas[i + 1][1].strip()
                i += 1
                extras += 1
            nome = re.sub(r"\s*[-–]\s*([A-Z]{2,12})$", r" - \1", nome)
            orgao = re.sub(r"\s+", " ", nome)
            atual = None
            i += 1
            continue
        # Dentro de uma retificação, os títulos citados não abrem um novo ato
        dentro_retificacao = (atual and re.search(r"RETIFICA", atual["titulo"], re.I)
                              and len(atual["linhas"]) < 15
                              and not any(re.match(r"Salvador,\s+\d", x) for x in atual["linhas"]))
        # Início de um ato
        if maiusculo(l[:60]) and not dentro_retificacao:
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
        m = re.search(r"-\s*([A-Z]{2,12})$", a["orgao"])
        a["sigla"] = m.group(1) if m else (a["orgao"].title()[:45] or "Órgão não identificado")
    return atos


def eh_relevante(ato):
    completo = ato["titulo"] + " " + ato["texto"]
    if RE_DESCARTAR.search(completo):
        return False
    if ato["topico"] != "outros" and (RE_SEMPRE.search(ato["orgao"]) or RE_PROCESSO_SEMPRE.search(completo)):
        ato["termo"] = "órgão " + ", ".join(ORGAOS_SEMPRE)
        return True
    termos = {m.group(0).lower() for m in RE_TIC.finditer(RE_FALSO.sub(" ", completo))}
    ato["termo"] = ", ".join(sorted(termos))
    # "Outros atos" (portarias, decretos...) exigem ao menos 2 termos diferentes
    return len(termos) >= (2 if ato["topico"] == "outros" else 1)


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
    valor = campo(r"VALOR[^:R]{0,30}:?\s*(R\$\s?[\d.]+,\d{2})", t, 30) \
        or campo(r"valor (?:total|global|estimado|mensal|anual)[^R]{0,20}(R\$\s?[\d.]+,\d{2})", t, 30) \
        or campo(r"(R\$\s?[\d.]+,\d{2})", t, 30)
    prazo = campo(r"((?:at[ée] o dia|abertura da sess[ãa]o no dia|sess[ãa]o p[úu]blica (?:no dia|em))\s+\d{1,2}(?:/\d{2}/\d{4}| de \w+ de \d{4})(?:,? [àa]s \d{1,2}[:h]\d{0,2}h?)?)", t, 70)

    natureza = ""
    if re.search(r"rescis", t + a["titulo"], re.I):
        natureza = " · *rescisão*"
    elif re.search(r"prorroga", t + a["titulo"], re.I):
        natureza = " · prorrogação"
    linha = f"• *{a['sigla']}* – {titulo_legivel(a['titulo'])}{natureza} _(pág. {a['pagina']})_"
    if os.environ.get("DIAGNOSTICO") == "sim":
        linha += f"\n   🔎 _motivo: {a.get('termo', '')}_"
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
    """Usa OpenAI (OPENAI_API_KEY) ou Anthropic (ANTHROPIC_API_KEY), o que estiver cadastrado."""
    material = "\n\n".join(
        f"[{i}] TIPO: {a['topico']} | ÓRGÃO: {a['orgao']} | PÁGINA: {a['pagina']}\n{a['titulo']}\n{a['texto'][:3000]}"
        for i, a in enumerate(atos, 1))
    prompt = (
        "Você recebe atos do Diário Oficial do Município de Salvador pré-selecionados por "
        "tratarem de tecnologia. Monte um apanhado para uma equipe de contratações de TIC.\n"
        "Regras de seleção:\n"
        "- Descarte atos sem relação real com tecnologia, exceto contratos, licitações e "
        "resultados cujo órgão responsável seja a SEMIT/SMART, que ficam sempre.\n"
        "- O campo ÓRGÃO foi detectado automaticamente e pode estar errado. Se o texto do ato "
        "indicar outro órgão responsável (ex.: 'PREGÃO ELETRÔNICO - SEMGE', 'PROCESSO Nº ...-SMED', "
        "e-mail institucional, assinatura do secretário), use o órgão indicado no texto.\n"
        "- Se um ato parecer misturar trechos de atos diferentes, use só a parte coerente com o título.\n"
        "Formato (Google Chat):\n"
        "- Tópicos nesta ordem, omitindo os vazios:\n"
        "  📢 *Licitações, cotações e editais*\n  📝 *Contratos, aditivos e apostilamentos*\n"
        "  ✅ *Resultados, homologações e atas*\n  📌 *Outros atos*\n"
        "- Cada item em até 3 linhas curtas:\n"
        "  • *SIGLA* – Tipo e número do ato em letras normais, não maiúsculas (ex.: Aviso de cotação nº 016/2026). "
        "Se for rescisão, prorrogação, suspensão, revogação ou anulação, acrescente ' · *rescisão*' (etc.). "
        "Termine com _(pág. N)_\n"
        "  Objeto: resumo do objeto em no máximo 200 caracteres\n"
        "  Empresa · Valor · uma única data, só os que existirem, separados por ' · '\n"
        "- Datas: no máximo UMA por item, a mais útil para quem acompanha (sessão de abertura, "
        "prazo final de propostas ou vigência). Nunca inclua data de assinatura, de publicação, "
        "de homologação, nem etapas intermediárias (envio de propostas, início da disputa). "
        "Escreva assim: 'Sessão 30/09/2026 às 9h30' ou 'Propostas até 21/09/2026'.\n"
        "- Retificações: um único item, dizendo em uma linha o que mudou (de X para Y) e citando "
        "a publicação corrigida. Nunca separe 'onde se lê' e 'leia-se' em itens diferentes.\n"
        "- Não inclua telefones, e-mails, fundamentação legal nem nomes de servidores.\n"
        "- Separe itens com uma linha em branco. Use *negrito* e _itálico_; sem # e sem tabelas.\n"
        "- Não invente dados. Se nada for relevante, responda apenas: Nenhum ato de TIC identificado.\n\n"
        + material[:150000])
    if os.environ.get("OPENAI_API_KEY"):
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                     "Content-Type": "application/json"},
            json={"model": os.environ.get("OPENAI_MODEL") or "gpt-5-mini",
                  "max_completion_tokens": 8000,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=300)
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()

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
    todos = separar_atos(ler_pdf(conteudo_pdf))
    atos = [a for a in todos if eh_relevante(a)]
    if os.environ.get("DIAGNOSTICO") == "sim":
        print(f"=== DIAGNÓSTICO: {len(todos)} atos lidos, {len(atos)} selecionados ===")
        for a in todos:
            print(f"[{'X' if a in atos else ' '}] pág {a['pagina']} | {a['topico']} | {a['orgao'][:50]} | {a['titulo'][:70]} | {a.get('termo','')}")
    if not atos:
        corpo = "Nenhum ato de TIC identificado nesta edição."
    elif os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
        try:
            corpo = montar_com_ia(atos)
            if not corpo:
                raise ValueError("resposta vazia da IA")
        except Exception as e:
            # Se a IA falhar (ex.: créditos acabaram), envia o resumo por regras
            print(f"AVISO: IA indisponível ({e}). Usando resumo por regras.")
            corpo = montar_por_regras(atos) + "\n\n_⚠️ Resumo sem IA: a IA não respondeu nesta edição._"
    else:
        corpo = montar_por_regras(atos)
    # Conta os itens da mensagem final (depois do filtro da IA, se houver)
    n = len(re.findall(r"^\s*•", corpo, re.M))
    qtd = f" · {n} ato(s) de TIC" if n else ""
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
