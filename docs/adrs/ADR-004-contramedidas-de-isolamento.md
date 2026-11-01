# ADR-004: Contramedidas para a falta de isolamento na saga

**Status:** Aceito
**Data:** 2026-11-01

## Contexto

Uma saga é ACD, não ACID: cada passo confirma localmente e fica visível antes do fim do processo. Sem isolamento, surgem anomalias:

- **atualização perdida**: o gestor edita o preço enquanto a compra congela o preço antigo;
- **leitura suja**: outra compra enxerga um anúncio que está no meio de uma saga que será compensada;
- **leitura não repetível**: o comprador vê uma versão na vitrine e paga por outra.

A aula "Lidando com a falta do I do ACID" apresenta contramedidas: trava semântica, atualizações comutativas, visão pessimista, releitura de valor, arquivo de versões e decisão por valor.

## Alternativas avaliadas

| Contramedida | Usada? | Onde |
|---|---|---|
| **Trava semântica** | Sim | `RESERVADO` (anúncio), `BLOQUEADO` (veículo) e `PROCESSANDO` (compra) são estados visíveis que impedem outra compra e a edição enquanto a saga não termina |
| **Releitura de valor** | Sim | O bloqueio (T2) confere se a versão vista pelo comprador ainda é a atual no Catálogo; se mudou, a saga compensa e responde 409 |
| **Visão pessimista** (ordem dos passos) | Sim | Os passos que podem falhar por regra de negócio (reserva, versão) vêm antes do pivô; o anúncio sai da vitrine antes de qualquer chamada externa |
| Atualizações comutativas | Não | Não há contadores ou saldos a somar |
| Arquivo de versões | Não | A ordem das operações é garantida pelos estados e pela versão |
| Decisão por valor | Não | Todas as compras têm o mesmo risco |

## Decisão

Adotar trava semântica, releitura de valor e visão pessimista, como na tabela. Também:

- a trava semântica é sempre liberada por uma compensação ou pelo fim da saga, nunca por tempo apenas (a expiração é uma compensação explícita, [ADR-006](ADR-006-expiracao-e-recuperacao-de-sagas.md));
- a edição no Catálogo de veículo `BLOQUEADO` ou `VENDIDO` responde 409 (RN-02), o que fecha a janela da atualização perdida do lado do principal;
- a compra informa `versao`; sem ela, a requisição é inválida (422).

## Consequências

### Positivas
- Nenhuma compra é feita por preço ou descrição diferentes do que o comprador viu.
- Nenhum veículo é vendido duas vezes, mesmo com a saga em andamento.

### Negativas
- Uma edição do gestor logo antes de uma compra faz a compra falhar com 409, e o comprador precisa consultar de novo.
- Durante a saga, o veículo some da vitrine mesmo que a compra venha a ser compensada.

## Mitigações
- A resposta 409 traz um `type` específico (`anuncio-alterado`) para o front-end recarregar o anúncio.
- A parte síncrona da saga leva milissegundos; a janela em que o anúncio some sem motivo é curta.
