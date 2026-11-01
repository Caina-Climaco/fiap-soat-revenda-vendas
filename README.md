# Revenda de Veículos: serviço de venda

Trabalho Substitutivo do Tech Challenge, FIAP PósTech Software Architecture (SOAT), Fase 4.
Autor: Cainã Clímaco, RM366473 (trabalho individual).

> **Esta entrega tem dois repositórios.** Este é o do **serviço de venda** (listagens, compra e orquestração da saga, com MongoDB isolado). O **serviço principal** (cadastro e edição de veículos, cobrança e webhook de pagamento), a plataforma comum e a documentação da solução estão em **[fiap-soat-revenda-principal](https://github.com/Caina-Climaco/fiap-soat-revenda-principal)**.

**Em construção.** O desenho já está documentado:

| Documento | Conteúdo |
|---|---|
| [Modelagem da solução](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/blob/main/docs/02-modelagem-ddd.md) | Contextos, agregados e a saga da compra |
| [Contratos da API](https://github.com/Caina-Climaco/fiap-soat-revenda-principal/blob/main/docs/05-api.md) | Rotas públicas e internas dos dois serviços |
| [ADRs deste serviço](docs/adrs/README.md) | MongoDB, saga orquestrada, réplica de leitura, isolamento, CPF e recuperação de sagas |
