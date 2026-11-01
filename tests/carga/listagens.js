// Teste de carga das listagens (RNF-10): p95 abaixo de 300 ms pelo Kong.
// Ritmo de 8 requisicoes por segundo (480 por minuto), abaixo do rate limiting de 600 por
// minuto por IP que o Kong aplica na vitrine: o teste mede o servico, nao a borda.
// Execucao: scripts/windows/carga-listagens.ps1 (k6 em container na rede do kind).
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE_URL || "http://revenda-control-plane:30080";

export const options = {
  scenarios: {
    vitrine: {
      executor: "constant-arrival-rate",
      rate: 8,
      timeUnit: "1s",
      duration: "1m",
      preAllocatedVUs: 10,
      maxVUs: 20,
    },
  },
  thresholds: {
    "http_req_duration{lista:a-venda}": ["p(95)<300"],
    "http_req_duration{lista:vendidos}": ["p(95)<300"],
    http_req_failed: ["rate<0.01"],
  },
};

export default function () {
  const lista = Math.random() < 0.7 ? "a-venda" : "vendidos";
  const deslocamento = Math.floor(Math.random() * 3) * 20;
  const resposta = http.get(`${BASE}/api/v1/veiculos/${lista}?limite=20&deslocamento=${deslocamento}`, {
    tags: { lista },
  });
  check(resposta, { "status 200": (r) => r.status === 200 });
}
