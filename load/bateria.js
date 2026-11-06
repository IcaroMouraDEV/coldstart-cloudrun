// Roteiro k6 de uma bateria de um cenário.
//
//   k6 run -e CENARIO=a -e BATERIA=1 -e BASE_URL=https://gw-a-... \
//          -e FIREBASE_API_KEY=... -e SENHA_TESTE=... \
//          --out csv=results/a-b1.csv.gz load/bateria.js
//
// 18 ciclos de 10 min de tráfego + 30 min parados, com taxas intercaladas
// (baixo, médio, alto, ...), executor constant-arrival-rate (modelo aberto).
// A latência de cada requisição vai para a Trend `latencia`, com as tags
// regiao, pausa, cenario, bateria, ciclo, nivel, servico, operacao e cold.
// instancia e idade_ms vão como metadata (coluna `metadata` do CSV): não criam séries
// de métricas, e a memória do k6 não cresce com o número de requisições.

import http from 'k6/http';
import { sleep } from 'k6';
import { Trend } from 'k6/metrics';
import exec from 'k6/execution';

const CENARIO = __ENV.CENARIO;
const BATERIA = __ENV.BATERIA;
const BASE_URL = (__ENV.BASE_URL || '').replace(/\/$/, '');
const FIREBASE_API_KEY = __ENV.FIREBASE_API_KEY;
const SENHA_TESTE = __ENV.SENHA_TESTE;
const REGIAO = __ENV.REGIAO || 'us-central1';
// Uma conta por VU pré-alocado: a cota de verificação de senha do Firebase é por conta
// (com 20 contas, 35–70% dos logins falharam nos níveis médio e alto).
const USUARIOS = Number(__ENV.USUARIOS || 300);
const EMAIL_PREFIXO = __ENV.EMAIL_PREFIXO || 'k6';
const POSTS_SEED = Number(__ENV.POSTS_SEED || 1000);

const CICLOS = Number(__ENV.CICLOS || 18);
const TRAFEGO_MIN = Number(__ENV.TRAFEGO_MIN || 10);
const PAUSA_MIN = Number(__ENV.PAUSA_MIN || 30);

// VUs pré-alocados em todos os níveis: criá-los sob demanda durante um cold start
// (requisições presas por ~8 s) era lento e fazia o k6 descartar iterações.
const VUS_PRE = Number(__ENV.VUS_PRE || 300);
const VUS_MAX = Number(__ENV.VUS_MAX || 600);
const NIVEIS = [
  { nome: 'baixo', taxa: 2 },
  { nome: 'medio', taxa: 8 },
  { nome: 'alto', taxa: 18 },
];

for (const [nome, valor] of Object.entries({ CENARIO, BATERIA, BASE_URL, FIREBASE_API_KEY, SENHA_TESTE })) {
  if (!valor) throw new Error(`variável ${nome} não definida (-e ${nome}=...)`);
}

function cenarios() {
  const resultado = {};
  for (let i = 0; i < CICLOS; i++) {
    const nivel = NIVEIS[i % NIVEIS.length];
    const ciclo = String(i + 1);
    resultado[`ciclo_${ciclo.padStart(2, '0')}`] = {
      executor: 'constant-arrival-rate',
      exec: 'requisicao',
      rate: nivel.taxa,
      timeUnit: '1s',
      duration: `${TRAFEGO_MIN}m`,
      startTime: `${i * (TRAFEGO_MIN + PAUSA_MIN)}m`,
      preAllocatedVUs: VUS_PRE,
      maxVUs: VUS_MAX,
      gracefulStop: '65s', // deixa terminar requisições de até 60 s
      tags: { ciclo, nivel: nivel.nome },
    };
  }
  return resultado;
}

export const options = {
  scenarios: cenarios(),
  tags: { regiao: REGIAO, pausa: String(PAUSA_MIN), cenario: CENARIO, bateria: String(BATERIA) },
  discardResponseBodies: true,
  // sem url/name nas tags de sistema: cada post (seed-XXXX e ids criados) virava uma série nova
  // e a memória do k6 crescia ~70 MB por ciclo. Não muda a Trend `latencia` nem a coluna status do CSV.
  systemTags: ['status', 'method', 'scenario', 'expected_response', 'error', 'error_code', 'check', 'group'],
  // sem thresholds: o SLO é avaliado na análise, não no k6
};

const latencia = new Trend('latencia', true);

// ---------- tokens do Firebase (fora do gateway, não entram em `latencia`) ----------

// FIREBASE_AUTH_EMULATOR (ex.: localhost:9099) aponta para o emulador do Firebase Auth, em testes.
const PREFIXO_FIREBASE = __ENV.FIREBASE_AUTH_EMULATOR ? `http://${__ENV.FIREBASE_AUTH_EMULATOR}/` : 'https://';
const URL_LOGIN = `${PREFIXO_FIREBASE}identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${FIREBASE_API_KEY}`;
const URL_REFRESH = `${PREFIXO_FIREBASE}securetoken.googleapis.com/v1/token?key=${FIREBASE_API_KEY}`;
const MARGEM_RENOVACAO_MS = 5 * 60 * 1000;

let sessao = null; // por VU: { email, idToken, refreshToken, expiraEm }

function emailDoVu() {
  const i = (exec.vu.idInTest - 1) % USUARIOS;
  return `${EMAIL_PREFIXO}-${String(i).padStart(3, '0')}@coldstart.test`;
}

function registraSessao(idToken, refreshToken, expiraSeg) {
  sessao = { email: emailDoVu(), idToken, refreshToken, expiraEm: Date.now() + Number(expiraSeg) * 1000 };
}

function token() {
  if (sessao && Date.now() < sessao.expiraEm - MARGEM_RENOVACAO_MS) return sessao.idToken;
  const params = { responseType: 'text', tags: { servico: 'firebase' } };

  if (sessao) {
    const r = http.post(URL_REFRESH, { grant_type: 'refresh_token', refresh_token: sessao.refreshToken },
      { ...params, tags: { servico: 'firebase', operacao: 'refresh' } });
    if (r.status === 200) {
      const d = r.json();
      registraSessao(d.id_token, d.refresh_token, d.expires_in);
      return sessao.idToken;
    }
  }
  // Até 3 tentativas: um 5xx/429 transitório do Firebase abortaria a iteração (fora da medição)
  let r;
  for (let tentativa = 1; tentativa <= 3; tentativa++) {
    r = http.post(URL_LOGIN, JSON.stringify({ email: emailDoVu(), password: SENHA_TESTE, returnSecureToken: true }),
      { ...params, headers: { 'Content-Type': 'application/json' }, tags: { servico: 'firebase', operacao: 'signin' } });
    if (r.status === 200 || (r.status < 500 && r.status !== 429)) break;
    sleep(tentativa);
  }
  if (r.status !== 200) throw new Error(`login no Firebase falhou: ${r.status} ${r.body}`);
  const d = r.json();
  registraSessao(d.idToken, d.refreshToken, d.expiresIn);
  return sessao.idToken;
}

// ---------- mistura de requisições ----------

const criados = []; // posts criados por este VU, alvos dos DELETE

function seedAleatorio() {
  return `seed-${String(Math.floor(Math.random() * POSTS_SEED)).padStart(4, '0')}`;
}

function corpoPost() {
  return JSON.stringify({ titulo: `k6 ${CENARIO}-${BATERIA}`, conteudo: 'x'.repeat(200) });
}

function headers(autenticado) {
  const h = { 'Content-Type': 'application/json' };
  if (autenticado) h.Authorization = `Bearer ${token()}`;
  return h;
}

// Sorteia a operação: 30% auth (2/3 verify, 1/3 login);
// 70% backend: 70% leitura (80% por id, 20% listagem) e 30% escrita (50% POST, 30% PUT, 20% DELETE).
function sorteia() {
  const r = Math.random();
  if (r < 0.30) return r < 0.20 ? 'verify' : 'login';
  const b = Math.random();
  if (b < 0.70) return b < 0.56 ? 'get' : 'list';
  const e = Math.random();
  if (e < 0.50) return 'post';
  if (e < 0.80) return 'put';
  return criados.length ? 'delete' : 'post';
}

function envia(operacao) {
  switch (operacao) {
    case 'verify':
      return ['auth', http.get(`${BASE_URL}/auth/verify`, { headers: headers(true), timeout: '61s' })];
    case 'login':
      return ['auth', http.post(`${BASE_URL}/auth/login`,
        JSON.stringify({ email: emailDoVu(), senha: SENHA_TESTE }),
        { headers: headers(false), timeout: '61s' })];
    case 'get':
      return ['backend', http.get(`${BASE_URL}/posts/${seedAleatorio()}`, { timeout: '61s' })];
    case 'list':
      return ['backend', http.get(`${BASE_URL}/posts`, { timeout: '61s' })];
    case 'post':
      return ['backend', http.post(`${BASE_URL}/posts`, corpoPost(),
        { headers: headers(true), timeout: '61s', responseType: 'text' })];
    case 'put':
      return ['backend', http.put(`${BASE_URL}/posts/${seedAleatorio()}`, corpoPost(),
        { headers: headers(true), timeout: '61s' })];
    case 'delete':
      return ['backend', http.del(`${BASE_URL}/posts/${criados.pop()}`, null,
        { headers: headers(true), timeout: '61s' })];
  }
}

export function requisicao() {
  const operacao = sorteia();
  if (operacao !== 'get' && operacao !== 'list' && operacao !== 'login') token(); // renova antes de medir
  const [servico, res] = envia(operacao);

  const cold = res.headers['X-Cold-Start'];
  const metadata = exec.vu.metrics.metadata;
  metadata.instancia = res.headers['X-Instance-Id'] || '';
  metadata.idade_ms = res.headers['X-Instance-Age-Ms'] || ''; // separa cold start real de instância pré-aquecida
  latencia.add(res.timings.duration, {
    servico,
    operacao,
    status: String(res.status),
    cold: cold === '1' || cold === '0' ? cold : 'desconhecido',
  });
  delete metadata.instancia;
  delete metadata.idade_ms;

  if (operacao === 'post' && res.status === 201) criados.push(res.json('id'));
}
