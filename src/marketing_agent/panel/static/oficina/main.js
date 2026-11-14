import {EstadoCliente} from './estado.js';
import {Coluna} from './coluna.js';

const raiz = document.querySelector('#oficina');
export const coluna = raiz ? new Coluna(raiz) : null;
let ultimoSucesso = new Date(), avisoTimer;
function vigiarLigacao() {
  clearTimeout(avisoTimer);
  avisoTimer = setTimeout(() => coluna.marcarSemLigacao(ultimoSucesso), Math.max(0, 30000 - (Date.now() - ultimoSucesso.getTime())));
}
export const cliente = raiz ? new EstadoCliente({
  url: '/oficina/estado',
  aoMudar(estado) {
    clearTimeout(avisoTimer);
    ultimoSucesso = new Date();
    raiz.dataset.ligacao = 'ok'; raiz.dataset.falhas = '0';
    coluna.atualizar(estado);
    vigiarLigacao();
  },
  aoFalhar(falhas) {
    raiz.dataset.falhas = falhas;
    if (falhas >= 3 || Date.now() - ultimoSucesso.getTime() >= 30000) coluna.marcarSemLigacao(ultimoSucesso);
    vigiarLigacao();
  },
  aoTerminarSessao() {
    clearTimeout(avisoTimer);
    coluna.marcarSemLigacao(ultimoSucesso);
    document.querySelector('#estado').textContent = 'A sessão terminou. Recarregue a página para voltar a entrar.';
  }
}) : null;
if (cliente) {
  raiz.dataset.ligacao = 'a_ligar';
  cliente.iniciar();
  vigiarLigacao();
  window.addEventListener('pagehide', () => {cliente.parar(); clearTimeout(avisoTimer);});
  window.addEventListener('pageshow', event => {if (event.persisted) cliente.iniciar();});
}
