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
// Acesso direto ao Cloud Run, sem gateway (teste de diagnóstico da penalidade do B):
// com AUTH_URL e BACKEND_URL, cada requisição vai à URL do serviço com o ID token da conta
// de serviço da VM em Authorization, e o token do Firebase segue em X-Forwarded-Authorization,
// como o gateway faria.
const AUTH_URL = (__ENV.AUTH_URL || '').replace(/\/$/, '');
const BACKEND_URL = (__ENV.BACKEND_URL || '').replace(/\/$/, '');
const DIRETO = Boolean(AUTH_URL && BACKEND_URL);
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
// ATRASO_MIN adia o 1º ciclo (teste do limiar: o 1º ciclo também começa depois da pausa do caminho)
const ATRASO_MIN = Number(__ENV.ATRASO_MIN || 0);

// VUs pré-alocados em todos os níveis: criá-los sob demanda durante um cold start
// (requisições presas por ~8 s) era lento e fazia o k6 descartar iterações.
const VUS_PRE = Number(__ENV.VUS_PRE || 300);
const VUS_MAX = Number(__ENV.VUS_MAX || 600);
const TODOS_NIVEIS = [
  { nome: 'baixo', taxa: 2 },
  { nome: 'medio', taxa: 8 },
  { nome: 'alto', taxa: 18 },
];
// NIVEL=medio fixa um único nível em todos os ciclos (teste de diagnóstico)
const NIVEIS = __ENV.NIVEL ? TODOS_NIVEIS.filter((n) => n.nome === __ENV.NIVEL) : TODOS_NIVEIS;
if (!NIVEIS.length) throw new Error(`NIVEL inválido: ${__ENV.NIVEL}`);

const obrigatorias = { CENARIO, BATERIA, FIREBASE_API_KEY, SENHA_TESTE, ...(DIRETO ? {} : { BASE_URL }) };
for (const [nome, valor] of Object.entries(obrigatorias)) {
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
      startTime: `${ATRASO_MIN + i * (TRAFEGO_MIN + PAUSA_MIN)}m`,
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

// ---------- ID token do Cloud Run (só no acesso direto; fora da medição) ----------

const URL_METADATA = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity';
const tokensRun = {}; // por VU: audiência -> { token, expiraEm }

function tokenRun(audiencia) {
  const t = tokensRun[audiencia];
  if (t && Date.now() < t.expiraEm) return t.token;
  const r = http.get(`${URL_METADATA}?audience=${audiencia}`,
    { headers: { 'Metadata-Flavor': 'Google' }, responseType: 'text', tags: { servico: 'metadata' } });
  if (r.status !== 200) throw new Error(`ID token do metadata falhou: ${r.status}`);
  tokensRun[audiencia] = { token: r.body, expiraEm: Date.now() + 50 * 60 * 1000 }; // vale 1 h
  return r.body;
}

function base(servico) {
  if (!DIRETO) return BASE_URL;
  return servico === 'auth' ? AUTH_URL : BACKEND_URL;
}

// ---------- mistura de requisições ----------

const criados = []; // posts criados por este VU, alvos dos DELETE

function seedAleatorio() {
  return `seed-${String(Math.floor(Math.random() * POSTS_SEED)).padStart(4, '0')}`;
}

function corpoPost() {
  return JSON.stringify({ titulo: `k6 ${CENARIO}-${BATERIA}`, conteudo: 'x'.repeat(200) });
}

function headers(autenticado, servico) {
  const h = { 'Content-Type': 'application/json' };
  if (DIRETO) {
    h.Authorization = `Bearer ${tokenRun(base(servico))}`;
    if (autenticado) h['X-Forwarded-Authorization'] = `Bearer ${token()}`;
  } else if (autenticado) {
    h.Authorization = `Bearer ${token()}`;
  }
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
  const A = base('auth');
  const B = base('backend');
  switch (operacao) {
    case 'verify':
      return ['auth', http.get(`${A}/auth/verify`, { headers: headers(true, 'auth'), timeout: '61s' })];
    case 'login':
      return ['auth', http.post(`${A}/auth/login`,
        JSON.stringify({ email: emailDoVu(), senha: SENHA_TESTE }),
        { headers: headers(false, 'auth'), timeout: '61s' })];
    case 'get':
      return ['backend', http.get(`${B}/posts/${seedAleatorio()}`, { headers: headers(false, 'backend'), timeout: '61s' })];
    case 'list':
      return ['backend', http.get(`${B}/posts`, { headers: headers(false, 'backend'), timeout: '61s' })];
    case 'post':
      return ['backend', http.post(`${B}/posts`, corpoPost(),
        { headers: headers(true, 'backend'), timeout: '61s', responseType: 'text' })];
    case 'put':
      return ['backend', http.put(`${B}/posts/${seedAleatorio()}`, corpoPost(),
        { headers: headers(true, 'backend'), timeout: '61s' })];
    case 'delete':
      return ['backend', http.del(`${B}/posts/${criados.pop()}`, null,
        { headers: headers(true, 'backend'), timeout: '61s' })];
  }
}

export function requisicao() {
  const operacao = sorteia();
  if (operacao !== 'get' && operacao !== 'list' && operacao !== 'login') token(); // renova antes de medir
  if (DIRETO) tokenRun(base(operacao === 'verify' || operacao === 'login' ? 'auth' : 'backend'));
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
