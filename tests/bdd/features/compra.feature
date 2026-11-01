# language: pt
Funcionalidade: Compra de veículo coordenada pela saga
  Como comprador, quero comprar um veículo da vitrine informando meu CPF,
  e a compra só é efetivada quando o pagamento é confirmado; em qualquer falha,
  o que foi feito é desfeito e o veículo volta à vitrine.

  Contexto:
    Dado um veículo à venda por "119900.00" na vitrine

  Cenário: Caminho feliz, do pedido à efetivação
    Quando o comprador pede a compra com o CPF "529.982.247-25"
    Então a compra fica aguardando pagamento com um código de pagamento
    E o veículo sai da vitrine e fica bloqueado no catálogo
    Quando o processador informa que o pagamento foi efetuado
    Então a compra é efetivada com a data da venda
    E o veículo aparece entre os vendidos por "119900.00"
    E o catálogo registra o veículo como vendido

  Cenário: Pagamento cancelado pelo processador
    Quando o comprador pede a compra com o CPF "529.982.247-25"
    E o processador informa que o pagamento foi cancelado
    Então a compra é cancelada com o motivo "PAGAMENTO_CANCELADO"
    E o veículo volta à vitrine e é desbloqueado no catálogo

  Cenário: Reserva expirada sem pagamento
    Quando o comprador pede a compra com o CPF "529.982.247-25"
    E passam 31 minutos sem resultado do pagamento
    E a recuperação de sagas é executada
    Então a compra é cancelada com o motivo "RESERVA_EXPIRADA"
    E a cobrança é cancelada no principal
    E o veículo volta à vitrine e é desbloqueado no catálogo

  Cenário: Serviço principal fora do ar durante a compra
    Dado que o serviço principal está fora do ar
    Quando o comprador pede a compra com o CPF "529.982.247-25"
    Então a resposta é 503 com o problema "servico-principal-indisponivel"
    E o veículo continua na vitrine
