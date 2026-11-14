import {urlInterna} from './estado.js';
const ESTADOS = new Set(['a_trabalhar', 'erro', 'desatualizado', 'em_espera', 'inativo']);

/** Coluna HTML acessivel; a cena futura apenas acompanha esta selecao. */
export class Coluna {
  constructor(raiz, {aoSelecionar = () => {}} = {}) {
    this.raiz = raiz; this.aoSelecionar = aoSelecionar;
    this.avatares = [...raiz.querySelectorAll('.avatar')];
    this.papel = raiz.dataset.selecionado;
    this.semLigacao = false;
    this.dados = new Map(); this.recarga = Promise.resolve();
    this.avatares.forEach(a => {
      a.tabIndex = a.dataset.papel === this.papel ? 0 : -1;
      a.addEventListener('click', event => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault(); event.stopPropagation();
        this.selecionar(a.dataset.papel, {origem: 'coluna'});
      });
      a.addEventListener('keydown', event => this.tecla(event, a));
    });
  }
  tecla(event, avatar) {
    const indice = this.avatares.indexOf(avatar), total = this.avatares.length;
    const destinos = {ArrowDown: (indice + 1) % total, ArrowRight: (indice + 1) % total,
      ArrowUp: (indice + total - 1) % total, ArrowLeft: (indice + total - 1) % total,
      Home: 0, End: total - 1};
    if (!(event.key in destinos)) return;
    event.preventDefault();
    this.avatares.forEach(a => { a.tabIndex = -1; });
    const destino = this.avatares[destinos[event.key]];
    destino.tabIndex = 0; destino.focus();
  }
  selecionar(papel, {origem = 'coluna'} = {}) {
    if (!this.avatares.some(a => a.dataset.papel === papel)) return;
    this.papel = papel; this.raiz.dataset.selecionado = papel;
    this.avatares.forEach(a => {
      a.tabIndex = a.dataset.papel === papel ? 0 : -1;
      if (a.dataset.papel === papel) a.setAttribute('aria-current', 'true');
      else a.removeAttribute('aria-current');
    });
    this.recarregar();
    this.aoSelecionar(papel, {origem});
  }
  recarregar() {
    const papel = this.papel;
    // Serializar impede que uma resposta antiga substitua a selecao mais recente.
    this.recarga = this.recarga.catch(() => {}).then(() => {
      if (this.papel === papel) return window.htmx.ajax('GET', '/oficina/agente/' + papel, {target: '#painel-trabalho'}).then(() => {
        if (this.semLigacao) this.marcarSemLigacao(this.desde);
      });
    });
  }
  atualizar(equipa) {
    const recuperou = this.semLigacao;
    this.semLigacao = false;
    this.raiz.querySelector('.aviso-ligacao')?.remove();
    this.avatares.forEach(avatar => {
      const agente = equipa.agentes.find(a => a.papel === avatar.dataset.papel);
      if (!agente) return;
      const serializado = JSON.stringify(agente);
      const anterior = this.dados.get(agente.papel);
      this.dados.set(agente.papel, serializado);
      avatar.querySelector('.estado-texto').textContent = agente.estado_texto;
      avatar.querySelector('.avatar-nome').textContent = agente.nome;
      avatar.querySelector('.avatar-pendencias span').textContent = agente.pendencias;
      avatar.querySelector('.estado-icone').className = 'estado-icone estado-' + (ESTADOS.has(agente.estado) ? agente.estado : 'desatualizado');
      if (agente.papel === this.papel && (recuperou || anterior !== serializado)) this.recarregar();
    });
    const link = this.raiz.querySelector('.revisao-semanal');
    const url = urlInterna(equipa.revisao_semanal.url);
    if (url) link.setAttribute('href', url);
    link.querySelector('span').textContent = equipa.revisao_semanal.pendentes;
  }
  marcarSemLigacao(desde) {
    this.semLigacao = true; this.desde = desde;
    let aviso = this.raiz.querySelector('.aviso-ligacao');
    if (!aviso) {
      aviso = document.createElement('p'); aviso.className = 'aviso aviso-ligacao';
      aviso.setAttribute('role', 'status'); this.raiz.prepend(aviso);
    }
    aviso.textContent = 'Sem ligação desde ' + desde.toLocaleTimeString('pt-PT', {hour: '2-digit', minute: '2-digit'}) + ' — estados por confirmar';
    this.avatares.forEach(a => {
      a.querySelector('.estado-texto').textContent = 'Estado por confirmar';
      a.querySelector('.estado-icone').className = 'estado-icone estado-desatualizado';
    });
    this.raiz.querySelectorAll('#agente-trabalho .estado-texto').forEach(e => {e.textContent = 'Estado por confirmar';});
  }
}
