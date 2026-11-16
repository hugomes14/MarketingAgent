/** URLs de dados devem continuar na mesma origem, incluindo normalizacao do browser. */
export function urlInterna(valor) {
  return typeof valor === 'string' && /^\/(?!\/)/.test(valor) && !/[\\\u0000-\u0020\u007f]/.test(valor) ? valor : null;
}

/** O marcador do servidor so avanca com uma revisao revista nova (nunca com a janela dos cinco). */
function marcadorMaisRecente(antes, agora) {
  if (!agora) return false;
  if (!antes) return true;
  // `em` tem formato fixo (microssegundos, UTC): a ordem das cadeias e a ordem dos instantes.
  return agora.em > antes.em || (agora.em === antes.em && agora.id > antes.id);
}

/** Apenas um marcador de revisao mais novo entre snapshots representa uma entrega. */
export function detetarTransicoes(anterior, atual) {
  if (!anterior) return [];
  const marcador = estado => estado.agentes.find(a => a.papel === 'revisor')?.ultima_entrega || null;
  const agora = marcador(atual);
  if (!marcadorMaisRecente(marcador(anterior), agora)) return [];
  return [{tipo: 'entrega_pasta', de: 'copywriter', para: 'revisor', id: agora.post_id}];
}

/** Sondagem sequencial: uma resposta termina antes de agendar o pedido seguinte. */
export class EstadoCliente {
  constructor({url, intervaloMs = 5000, aoMudar, aoFalhar, aoTerminarSessao}) {
    this.url = urlInterna(url);
    if (!this.url) throw new Error('URL de estado invalido');
    Object.assign(this, {intervaloMs, aoMudar, aoFalhar, aoTerminarSessao});
    this.ativo = false; this.emCurso = false; this.falhas = 0; this.etag = null; this.estado = null;
    this.visibilidade = () => {
      clearTimeout(this.timer);
      if (!document.hidden) this.pedir();
    };
  }
  iniciar() {
    if (this.ativo) return;
    this.ativo = true;
    document.addEventListener('visibilitychange', this.visibilidade);
    this.pedir();
  }
  parar() {
    this.ativo = false;
    clearTimeout(this.timer);
    this.abortador?.abort();
    document.removeEventListener('visibilitychange', this.visibilidade);
  }
  async pedir() {
    if (!this.ativo || document.hidden || this.emCurso) return;
    clearTimeout(this.timer);
    this.emCurso = true;
    this.abortador = new AbortController();
    try {
      const response = await fetch(this.url, {cache: 'no-store', credentials: 'same-origin',
        headers: this.etag ? {'If-None-Match': this.etag} : {}, signal: this.abortador.signal});
      if (!this.ativo) return;
      if (response.status === 401 || response.status === 403) {
        this.parar(); this.aoTerminarSessao(); return;
      }
      if (response.status !== 304) {
        if (!response.ok) throw new Error('Estado indisponivel');
        const estado = await response.json();
        if (!this.ativo) return;
        const transicoes = detetarTransicoes(this.estado, estado);
        this.estado = estado;
        this.etag = response.headers.get('ETag');
        this.aoMudar(estado, transicoes);
      } else if (this.estado) {
        // 304 confirma frescura sem modificar o conteudo ou repetir transicoes.
        this.aoMudar(this.estado, []);
      } else throw new Error('304 sem snapshot');
      this.falhas = 0;
    } catch (erro) {
      if (this.ativo) this.aoFalhar(++this.falhas);
    } finally {
      this.emCurso = false;
      if (this.ativo && !document.hidden) {
        const atraso = this.falhas ? Math.min(60000, 10000 * 2 ** (this.falhas - 1)) : this.intervaloMs;
        this.timer = setTimeout(() => this.pedir(), atraso);
      }
    }
  }
}
