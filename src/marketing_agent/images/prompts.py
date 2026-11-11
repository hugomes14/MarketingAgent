"""Prompt fixo do fundo gerado por IA.

O enquadramento vive aqui, em codigo: nem o Designer nem o utilizador escrevem o prompt final.
O unico texto variavel e o `tema_fundo`, que entra neutralizado, entre aspas e como "atmosfera,
cores e formas". O enquadramento proibe pessoas, texto, logotipos e produtos reais: o gerador
so faz fundos abstratos (regra da marca, nao um detalhe de estilo).
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Final, Literal

Estilo = Literal["aguarela", "fotografico_desfocado", "minimalista", "textura_papel"]

MAX_THEME_CHARS: Final = 120
# Entrada bruta maxima: limita o trabalho (normalizacao e regex) antes de neutralizar. Largamente
# acima de 120 para tolerar tema com espacos e caracteres invisiveis que a neutralizacao remove.
_MAX_RAW_THEME_CHARS: Final = 1000

# Descricao fixa de cada estilo; o `estilo` do Designer so escolhe uma destas chaves.
STYLE_DESCRIPTIONS: Final[dict[str, str]] = {
    "aguarela": "aguarela suave, com manchas de cor translúcidas e contornos difusos",
    "fotografico_desfocado": (
        "fotografia desfocada, com luz suave, bokeh de cores e profundidade de campo reduzida"
    ),
    "minimalista": "minimalista, com formas geométricas simples e poucas cores planas",
    "textura_papel": "textura de papel artesanal, com fibras subtis e tons suaves",
}

_FRAME: Final = (
    "Imagem de fundo abstrata para uma publicação de redes sociais, formato vertical.\n"
    "Estilo: {estilo}.\n"
    'Tema, apenas como atmosfera, cores e formas: "{tema}".\n'
    "Proibido: pessoas, rostos, mãos ou corpos; texto, letras ou números; logótipos ou marcas;\n"
    "produtos, embalagens, comida, montras, edifícios ou interiores reconhecíveis.\n"
    "Zona central limpa e com pouco detalhe, para um título."
)

# Caracteres removidos do tema: delimitadores do enquadramento (aspas retas e tipograficas,
# imitaveis a olho), marcacao HTML/JSON/Markdown, o acento grave e a barra invertida (`azul\`
# escaparia a aspa de fecho do enquadramento).
_STRIPPED_CHARS: Final = frozenset('<>{}[]"`\\“”„‟‘’‚‛«»‹›＂')
# Categorias Unicode removidas: controlo (Cc), formato (Cf: largura zero, bidi, etiquetas) e
# substitutos soltos (Cs), que nem sequer se codificam em UTF-8.
_DROPPED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs"})

# Lista fixa de TLDs (nao `\w+\.\w{2,}`, que recusaria "verao 2.0" ou "azul.Escuro"): os pedidos
# comuns em Portugal e os encurtadores/dominios baratos mais usados em burlas e anuncios.
_TLDS: Final = (
    "com|pt|net|org|io|eu|co|ly|xyz|me|info|app|dev|ai|tv|to|cc|biz|us|uk|es|fr|de|br|link|site"
    "|online|store|shop|top|live|cloud|page|ru|cn|in"
)
_LINK_PATTERNS: Final = (
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"www\.", re.IGNORECASE),
    re.compile(rf"\w+\.(?:{_TLDS})\b", re.IGNORECASE),
    # Reforco alem do pedido: qualquer esquema (ftp://), URL relativa a protocolo (//host) e
    # e-mails (@) nao sao atmosfera nem cores, e nao ha TLD a listar.
    re.compile(r"://|//|@"),
    # Encurtadores com TLD fora da lista (encurta.zz/abc) e enderecos IPv4.
    re.compile(r"\w\.\w{2,}/"),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
)
_WHITESPACE: Final = re.compile(r"\s+")

_INVALID_THEME_MESSAGE: Final = "tema_fundo invalido"


class InvalidTheme(ValueError):
    """O `tema_fundo` nao pode entrar no prompt.

    A mensagem e fixa por motivo e nunca repete o tema (pode ser texto injetado).
    """


def _contains_link(text: str) -> bool:
    # NFKC dobra variantes de largura total (ｗｗｗ．x．pt); verifica as duas formas.
    candidates = (text, unicodedata.normalize("NFKC", text))
    return any(pattern.search(form) for form in candidates for pattern in _LINK_PATTERNS)


def neutralize_theme(text: str) -> str:
    """Devolve o tema numa so linha, sem marcacao, sem ligacoes e com no maximo 120 caracteres.

    Passos: NFC; espaco em branco (inclui quebras de linha e tabs) vira espaco, para que uma
    injecao em varias linhas nao cole palavras; removem-se os caracteres de controlo e de
    formato (Cc, Cf, Cs) e `<>{}[]`, aspas e acento grave; colapsam-se os espacos; recusam-se
    URLs, dominios e e-mails; recusa-se texto vazio ou com mais de 120 caracteres (contados em
    caracteres, nao em bytes, ja neutralizado).

    Raises:
        InvalidTheme: se o tema for vazio, longo demais ou tiver uma ligacao.
    """
    if len(text) > _MAX_RAW_THEME_CHARS:
        raise InvalidTheme(_INVALID_THEME_MESSAGE + ": demasiado longo")
    spaced = "".join(" " if char.isspace() else char for char in unicodedata.normalize("NFC", text))
    # NFC outra vez: remover um caracter invisivel pode juntar uma letra ao acento seguinte.
    # Assim o resultado e estavel (neutralizar de novo da o mesmo texto), o que `PromptFixo`
    # exige para aceitar o tema.
    kept = unicodedata.normalize(
        "NFC",
        "".join(
            char
            for char in spaced
            if unicodedata.category(char) not in _DROPPED_CATEGORIES and char not in _STRIPPED_CHARS
        ),
    )
    # Estas verificacoes correm sobre o texto final: remover caracteres so pode juntar pedacos
    # (pad<>aria.pt), nunca desfazer uma ligacao.
    theme = _WHITESPACE.sub(" ", kept).strip()
    if not theme:
        raise InvalidTheme(_INVALID_THEME_MESSAGE + ": vazio")
    if len(theme) > MAX_THEME_CHARS:
        raise InvalidTheme(f"{_INVALID_THEME_MESSAGE}: mais de {MAX_THEME_CHARS} caracteres")
    if _contains_link(theme):
        raise InvalidTheme(_INVALID_THEME_MESSAGE + ": ligacoes e dominios nao sao permitidos")
    return theme


# Credencial interna: so `build_background_prompt` a possui. Impede construir um `PromptFixo` com
# texto arbitrario por engano; nao e uma barreira contra codigo malicioso no mesmo processo.
_ISSUER: Final = object()

# Forma exata de um prompt valido: o enquadramento, uma das descricoes e um tema entre aspas sem
# quebras de linha nem aspas. `dataclasses.replace` copia a credencial, por isso o texto e
# verificado contra o enquadramento (e o tema contra `neutralize_theme`) em cada construcao.
_PROMPT_SHAPE: Final = re.compile(
    re.escape(_FRAME)
    .replace(
        re.escape("{estilo}"),
        "(?:" + "|".join(re.escape(d) for d in STYLE_DESCRIPTIONS.values()) + ")",
    )
    .replace(re.escape("{tema}"), r"(?P<tema>[^\n\"]+)")
)


def _is_issued_text(text: str, digest: str) -> bool:
    shape = _PROMPT_SHAPE.fullmatch(text)
    if shape is None or digest != hashlib.sha256(text.encode("utf-8")).hexdigest():
        return False
    try:
        return neutralize_theme(shape["tema"]) == shape["tema"]
    except InvalidTheme:
        return False


@dataclass(frozen=True)
class PromptFixo:
    """Prompt final de geracao; so `build_background_prompt` o constroi.

    `sha256` e o SHA-256 (hexadecimal) do `text` exato em UTF-8: identifica o fundo para
    reutilizacao e para a proveniencia. O construtor recusa (`TypeError`) texto que nao venha de
    `build_background_prompt`, tambem via `dataclasses.replace`: o texto tem de ter o
    enquadramento fixo, um tema ja neutralizado e o `sha256` correto.
    """

    text: str
    sha256: str
    _issued_by: object = field(default=None, repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        if self._issued_by is not _ISSUER or not _is_issued_text(self.text, self.sha256):
            raise TypeError("PromptFixo so pode ser construido por build_background_prompt")


def build_background_prompt(tema_fundo: str, estilo: Estilo) -> PromptFixo:
    """Constroi o prompt: enquadramento fixo + descricao fixa do estilo + tema neutralizado.

    Raises:
        InvalidTheme: se o tema nao passar em `neutralize_theme`.
        ValueError: se `estilo` nao for um dos estilos conhecidos.
    """
    description = STYLE_DESCRIPTIONS.get(estilo)
    if description is None:
        raise ValueError("estilo desconhecido")
    text = _FRAME.format(estilo=description, tema=neutralize_theme(tema_fundo))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return PromptFixo(text=text, sha256=digest, _issued_by=_ISSUER)
