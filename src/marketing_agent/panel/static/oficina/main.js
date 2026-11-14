import {EstadoCliente} from './estado.js';
import {Coluna} from './coluna.js';
import {chaveAparencia, guardar, restaurar, capturarCara} from './capturas.js';
import {temWebGL, lerEscolha, guardarEscolha, prefereMovimentoReduzido, vistaInicial} from './vista.js';

const raiz = document.querySelector('#oficina');
export let oficina = null;
let ultimoEstado = null;
export const coluna = raiz ? new Coluna(raiz, {
  aoSelecionar: (papel, {origem}) => {if (origem !== 'cena') oficina?.selecionar(papel);},
  aoSemLigacao: () => oficina?.marcarSemLigacao()
}) : null;
const personagens = raiz ? [...raiz.querySelectorAll('.avatar')].map(a => ({
  id: a.dataset.papel, name: a.querySelector('.avatar-nome').textContent,
  look: {hair: a.dataset.cabelo, torso: a.dataset.roupa, arms: a.dataset.roupa, style: a.dataset.estilo}
})) : [];
const caras = new Map();
async function restaurarCaras() {
  for (const pessoa of personagens) {
    const chave = await chaveAparencia(pessoa.look);
    const canvas = raiz.querySelector('.avatar[data-papel="' + pessoa.id + '"] .avatar-cara');
    caras.set(pessoa.id, {chave, canvas, restaurada: restaurar(pessoa.id, chave, canvas)});
  }
}
const carasRestauradas = restaurarCaras();
let ultimoSucesso = new Date(), avisoTimer;
function vigiarLigacao() {
  clearTimeout(avisoTimer);
  avisoTimer = setTimeout(() => coluna.marcarSemLigacao(ultimoSucesso), Math.max(0, 30000 - (Date.now() - ultimoSucesso.getTime())));
}
export const cliente = raiz ? new EstadoCliente({
  url: '/oficina/estado',
  aoMudar(estado, transicoes) {
    ultimoEstado = estado;
    oficina?.aplicarEstado(estado, transicoes);
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

const alternar = raiz?.querySelector('#vista-alternar');
const aviso = raiz?.querySelector('#oficina-lenta');
const contentor = raiz?.querySelector('#oficina-cena');
let vistaAtual = '2d', geracao = 0;

/** Cada montagem ou desmontagem invalida as anteriores, que terminam sem tocar na pagina. */
function desmontar3D() {
  geracao++;
  oficina?.desmontar(); oficina = null; contentor.hidden = true;
}

async function capturarCaras(instancia, ativa) {
  await carasRestauradas;
  for (const pessoa of personagens) {
    const cara = caras.get(pessoa.id);
    if (!ativa()) return;
    if (!cara.restaurada) {
      const pixels = await capturarCara(instancia, pessoa.id, cara.canvas);
      if (pixels) guardar(pessoa.id, cara.chave, pixels);
    }
  }
  raiz.dataset.carasProntas = '1';
}

/** A vista 2D permanece completa se WebGL ou o carregamento da cena falhar. */
async function montar3D() {
  const minha = ++geracao;
  const ativa = () => minha === geracao;
  let instancia = null;
  try {
    const {montar} = await import('./oficina3d.js');
    if (!ativa()) return;
    contentor.hidden = false;
    instancia = await montar(contentor, {personagens,
      aoSelecionar: papel => coluna.selecionar(papel, {origem: 'cena'}),
      aoQualidade: nivel => {aviso.hidden = nivel !== 'insuficiente';},
      reduzirMovimento: prefereMovimentoReduzido()});
    if (!ativa()) {instancia.desmontar(); return;}
    oficina = instancia;
    if (ultimoEstado) oficina.aplicarEstado(ultimoEstado, []);
    if (coluna.semLigacao) oficina.marcarSemLigacao();
    oficina.selecionar(coluna.papel, {focar: false});
    await capturarCaras(instancia, ativa);
  } catch (erro) {
    if (!ativa()) return;
    instancia?.desmontar(); desmontar3D(); mostrarVista('2d');
    document.querySelector('#estado').textContent = 'Não foi possível abrir a vista 3D. A vista 2D continua disponível.';
  }
}

function mostrarVista(vista) {
  vistaAtual = raiz.dataset.vista = vista;
  alternar.setAttribute('aria-pressed', String(vista === '3d'));
  if (vista === '2d') aviso.hidden = true;
}

function aplicarVista(vista) {
  mostrarVista(vista);
  if (vista === '3d') montar3D(); else desmontar3D();
}

function escolherVista(vista) {
  guardarEscolha(vista);
  aplicarVista(vista);
}

function iniciarVista() {
  const webgl = temWebGL();
  alternar.hidden = false;
  if (!webgl) {
    alternar.disabled = true;
    alternar.textContent = '3D indisponível neste browser';
  }
  alternar.addEventListener('click', () => escolherVista(vistaAtual === '3d' ? '2d' : '3d'));
  aviso.querySelector('button').addEventListener('click', () => escolherVista('2d'));
  aplicarVista(vistaInicial({webgl, escolha: lerEscolha(), movimentoReduzido: prefereMovimentoReduzido()}));
}

/** Quadro de medicao so existe com ?medir=1; a vista 2D nunca o carrega de outra forma. */
async function iniciarMedicao() {
  if (new URLSearchParams(location.search).get('medir') !== '1') return;
  const {iniciarMedidas} = await import('./medidas.js');
  iniciarMedidas(raiz.querySelector('.oficina-barra'),
    () => ({vista: vistaAtual, estatisticas: oficina?.estatisticas() ?? null}));
}

if (raiz) {
  iniciarVista();
  iniciarMedicao();
  // Uma pagina restaurada da cache recupera a vista 3D que o pagehide libertou.
  window.addEventListener('pageshow', event => {if (event.persisted && vistaAtual === '3d') montar3D();});
  window.addEventListener('pagehide', desmontar3D);
}
