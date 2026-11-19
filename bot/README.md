# Bot de paper trading (S16) — guia para o dono

> **AVISOS IMPORTANTES — leia primeiro**
>
> 1. **SÓ PAPEL (PAPER ONLY).** Este bot **não envia ordens reais**. Não existe no código
>    nenhuma forma de enviar uma ordem, nem de usar chaves de API. Se mudar `mode: live` no
>    `config.yaml`, o bot pára com a mensagem *"Real money needs the owner's 'go live'"*. Se
>    houver chaves da Binance no computador (variáveis de ambiente), o bot só avisa e ignora-as.
> 2. **O S16 enfraqueceu no 2.º ano fora da amostra.** No backtest: +0,442R por trade no 1.º
>    ano (2024-11..2025-10) e só **+0,051R** no 2.º ano (2025-11..2026-10). Perde em meses de
>    queda do BTC. O paper trading serve exatamente para ver se a vantagem ainda existe.
> 3. **Os mínimos nocionais são ESTIMATIVAS** (BTC 100 USDT, ETH 20 USDT, outras 5 USDT) até à
>    primeira leitura ao vivo do `exchangeInfo` da Binance. Nessa altura o bot substitui-os pelos
>    valores reais e escreve os dois (estimativa e real) em `logs/system.jsonl`.
> 4. **O bot só negoceia enquanto o PC está ligado e acordado.** Se o PC dormir, as entradas
>    desse período perdem-se (ficam registadas como "missed").
> 5. Com 24% de trades ganhadores, **3 perdas seguidas são frequentes**: o Shield vai parar o bot
>    (HALT) várias vezes por trimestre (no replay de 2026-05..2026-10: 9 paragens por 3 perdas
>    seguidas e 1 por perda diária em 6 meses). É normal; veja "Como retomar depois de um HALT".

Os comandos estão em inglês (é assim que o Windows os escreve); as explicações em português.

---

## 1. O que o bot faz

- De hora a hora (30–60 segundos depois de cada hora certa, com um atraso aleatório) lê dados
  **públicos** da Binance (velas de 1h, funding e regras do mercado) — sem conta, sem chaves.
- Usa **exatamente o mesmo código de sinais** do backtest (LAB): setup **S16** "pago para
  segurar" — compra uma das 10 maiores moedas quando, no fecho de uma vela de 4h, o preço está
  acima da média de 50 velas, essa média acima da de 200, e o funding liquidado é negativo. Sai
  quando a vela de 4h fecha abaixo da média de 50, quando o funding sobe acima de +0,01%/8h, ou
  no stop (2 × ATR abaixo da entrada).
- Cada mês recalcula o **universo**: as 10 maiores moedas cripto (sem ações/ETFs/índices/
  stablecoins) pelo volume dos 30 dias anteriores, pelo mesmo método do RADAR.
- Simula as ordens em papel: capital inicial **100 EUR = 110 USDT** (câmbio ESTIMATIVA 1,10),
  **1% de risco** por trade, taxas e slippage iguais ao backtest, funding real nas horas reais.
  Cada ordem leva o stop "anexado".
- O **Shield** aprova ou bloqueia cada trade e pode parar o bot (ver secção 7).

## 2. Instalação no Windows (uma vez)

1. **Python**: instale o Python **3.12, 3.13 ou 3.14** de <https://www.python.org/downloads/>
   (o 3.15 ainda não tem os pacotes necessários). No instalador, marque
   **"Add python.exe to PATH"**.
2. **Git**: instale o Git for Windows de <https://git-scm.com/download/win> (opções padrão).
3. Abra o **PowerShell** (tecla Windows, escreva `PowerShell`, Enter) e copie, uma linha de cada
   vez (troque `<URL-do-repositório>` pelo endereço que lhe deram):

   ```powershell
   cd $HOME
   git clone <URL-do-repositório> quant-desk
   cd quant-desk
   git checkout research/night-1        # ou o ramo que lhe indicarem
   py -3.13 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m bot --once
   ```

   (Se instalou o 3.12 ou o 3.14, escreva `py -3.12` ou `py -3.14` na linha do `venv`.)
   A última linha faz um único ciclo para confirmar que tudo funciona; deve escrever `ok`.

**Atualizar** mais tarde (quando lhe disserem que há uma versão nova):

```powershell
cd $HOME\quant-desk
git pull
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. Arrancar o bot (o único comando)

```powershell
cd $HOME\quant-desk
powershell -ExecutionPolicy Bypass -File .\run_bot.ps1
```

Deixe a janela aberta. O `run_bot.ps1` reinicia o bot automaticamente 60 segundos depois de
qualquer falha. Para **parar**: feche a janela (ou Ctrl+C).
Se o bot parar com um erro de configuração (`CONFIG ERROR`) ou por `mode: live`, o script não
reinicia — corrija o `config.yaml` e arranque de novo.

### Arrancar sozinho ao entrar no Windows (Agendador de Tarefas)

1. Tecla Windows → escreva **Agendador de Tarefas** (Task Scheduler) → Enter.
2. **Criar Tarefa Básica…** → Nome: `Paper bot` → Seguinte.
3. Disparador: **Quando eu iniciar sessão** → Seguinte.
4. Ação: **Iniciar um programa** → Seguinte.
   - Programa/script: `powershell.exe`
   - Adicionar argumentos:
     `-ExecutionPolicy Bypass -WindowStyle Minimized -File "C:\Users\<o-seu-utilizador>\quant-desk\run_bot.ps1"`
   - Iniciar em: `C:\Users\<o-seu-utilizador>\quant-desk`
5. Concluir. Depois abra a tarefa (duplo clique) → separador **Condições** → **desmarque**
   "Iniciar a tarefa apenas se o computador estiver ligado à corrente AC" se for um portátil
   que às vezes está na bateria. No separador **Definições**, desmarque "Parar a tarefa se
   for executada durante mais de…".

### O PC tem de estar acordado

O bot só trabalha enquanto o Windows está ligado e **não está em suspensão**. Para impedir a
suspensão quando o PC está ligado à corrente:

- **Definições → Sistema → Energia e bateria → Ecrã e suspensão →** "Quando ligado à corrente,
  colocar o dispositivo em suspensão após": **Nunca**. (O ecrã pode desligar-se; isso não faz mal.)
- Ou, no PowerShell **como administrador**:

  ```powershell
  powercfg /change standby-timeout-ac 0
  powercfg /change hibernate-timeout-ac 0
  ```

Se o PC dormir, ao acordar o bot recupera sozinho: os stops que teriam sido atingidos enquanto
dormia são registados ao preço do stop (como numa corretora, onde o stop fica na bolsa); as
entradas desse período são registadas como perdidas ("missed"); as saídas por sinal são feitas
com atraso, ao preço do momento ("signal_late").

## 4. O painel (dashboard)

```powershell
cd $HOME\quant-desk
.\.venv\Scripts\python.exe -m bot.dashboard
```

Cria o ficheiro `dashboard.html` (na pasta do bot) e abre-o no navegador. Não precisa de
internet nem de servidor. Mostra: estado do bot (a funcionar / PARADO com o motivo /
desatualizado), capital em EUR e USDT, resultado de hoje, 7 dias, 30 dias e total, posições
abertas, % de ganhos, drawdown máximo, perdas seguidas contra o limite de 3, curva de capital,
últimos 50 trades com o motivo de entrada e saída, eventos do Shield, "Saúde do setup"
(verde/amarelo/vermelho) e o universo top-10 do mês.

- Para o manter sempre atualizado: `.\.venv\Scripts\python.exe -m bot.dashboard --watch`
  (refaz o ficheiro a cada 5 minutos; a página recarrega-se sozinha a cada 5 minutos).
- **Atalho no ambiente de trabalho:** na pasta `quant-desk`, clique com o botão direito em
  `open_dashboard.bat` → **Mostrar mais opções → Enviar para → Ambiente de trabalho (criar
  atalho)**. Duplo clique no atalho abre o painel.
- Funciona também no telemóvel se abrir o ficheiro `dashboard.html` lá (por exemplo, copiado
  por OneDrive).

## 5. Relatório mensal

Uma vez por mês:

```powershell
cd $HOME\quant-desk
.\.venv\Scripts\python.exe -m bot.report --days 30
```

Mostra: número de trades, P&L (USDT e EUR), % de ganhos, drawdown máximo, a expectativa em
papel (R por trade) comparada com o backtest (+0,208R; 2.º ano +0,051R), os eventos do Shield,
e o teste **"primeiro sinal de que está a enfraquecer"** do cartão S16, em linguagem simples:

- **AMARELO (enfraquecer):** expectativa dos últimos 12 meses abaixo de +0,05R por trade, mais
  de 15 perdas seguidas, ou drawdown acima de 28%.
- **VERMELHO (parar o paper trading):** drawdown acima de 30% do máximo, ou expectativa dos
  últimos 100 trades abaixo de zero.
- Com menos de 30 trades o relatório diz "cedo demais para concluir" — com 24% de ganhos são
  precisos muitos trades para se ver alguma coisa.

## 6. Como ler os registos (pasta `logs\`)

Abra com o Bloco de Notas. Cada linha é um registo completo (formato JSON). Os ficheiros só
crescem; nunca são reescritos.

| ficheiro | o que tem |
|---|---|
| `decisions.jsonl` | cada sinal avaliado (a cada fecho de 4h, por moeda): os valores usados (`close`, `sma_fast`, `sma_slow`, `F8` = funding, `atr14`) e o motivo (`reason`): porque entrou ou porque não entrou |
| `trades.jsonl` | cada abertura (`"event": "open"`) e fecho (`"event": "close"`) de trade em papel: preços, stop, tamanho, funding, `pnl` em USDT, `R`, motivo da saída |
| `shield.jsonl` | cada bloqueio (`BLOCK`), paragem (`HALT`) e retoma (`RESET`), com o motivo |
| `equity.jsonl` | o capital de hora a hora (`equity_mark_usdt` = com as posições abertas ao preço atual) |
| `system.jsonl` | arranques, universo do mês, regras do mercado (estimativa vs. real) |
| `bot.log` | o mesmo que aparece na janela do bot |

O estado (posições abertas, capital) está em `state.json`. **Não edite este ficheiro**: se não
bater certo com `trades.jsonl`, o Shield pára o bot (é de propósito).

## 7. O que faz o Shield

O Shield é um módulo separado que **aprova ou bloqueia cada trade**. Bloqueia (e escreve o motivo
em `logs/shield.jsonl`) quando:

- o tamanho fica abaixo do mínimo nocional da moeda (ESTIMATIVA até à primeira leitura real);
- a soma das posições abertas ficaria acima de **3x** o capital;
- já há **3 posições** abertas;
- já há uma posição aberta nessa moeda;
- a ordem não tem um stop válido.

E **pára o bot inteiro (HALT)** quando:

- o resultado do dia (UTC) chega a **−3%**;
- há **3 perdas seguidas**;
- os dados estão desatualizados: a última vela de 1h fechada tem mais de 2 horas, ou **3 falhas
  seguidas** a ler dados (com nova tentativa a cada 5 minutos);
- o capital/posições em `state.json` não batem certo com `trades.jsonl`;
- há um erro inesperado no código de decisão.

Num HALT o bot cria um ficheiro chamado **`HALT`** na pasta `quant-desk`, não abre nem fecha
nada, e espera por si. As posições abertas mantêm o stop (como na bolsa).

## 8. Como retomar depois de um HALT

1. Veja o motivo: abra o painel, ou o ficheiro `HALT` (Bloco de Notas), ou a última linha de
   `logs\shield.jsonl`.
2. Se for falta de dados/internet, confirme que a internet voltou. Se for "mismatch" ou
   "exception", guarde a pasta `logs\` e o `state.json` e peça ajuda antes de retomar.
3. **Apague o ficheiro `HALT`**:

   ```powershell
   cd $HOME\quant-desk
   Remove-Item .\HALT
   ```

   (ou apague-o no Explorador de Ficheiros). Não é preciso reiniciar: na hora seguinte o bot
   vê que o ficheiro desapareceu, regista um `RESET`, põe a zero as perdas seguidas e as falhas,
   recomeça a contagem do dia, e recupera as velas que perdeu.

## 9. Mudar de setup ou de parâmetros

Tudo está em `config.yaml` (é o único ficheiro a editar). Para trocar de setup, mude `setup.id`
**e** `setup.params` (os nomes dos parâmetros são os do `src/lab/setups.py`). Os limites do
Shield, o risco (1%) e os custos só podem ser tornados **mais apertados**; se tentar
aliviá-los, o bot recusa arrancar com `CONFIG ERROR`. Atualize também `monitoring` e
`backtest_reference` com os números do cartão do novo setup.

## 10. macOS / Linux

```bash
cd ~/quant-desk
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./run_bot.sh                                  # arrancar (reinicia após falhas)
.venv/bin/python -m bot.report --days 30      # relatório
.venv/bin/python -m bot.dashboard             # painel
rm HALT                                       # retomar depois de um HALT
```

---

## Notas técnicas (para quem mantiver o código)

- Código: `bot/` (`runner.py` ciclo, `fills.py` simulação de ordens, `shield.py`,
  `market.py` velas/funding, `universe.py`, `datasource.py` REST, `replay.py` arquivo,
  `report.py`, `dashboard.py`, `live.py` stub). Sinais: `src/lab/setups.py` sem alterações.
  Universo: `src/radar/universe.py`/`symbols.py`/`classify.py` sem alterações.
- Testes (precisam de `data/clean`, o arquivo local; no PC do dono não é preciso):
  `.venv/bin/python -m pip install -r requirements-dev.txt` e `.venv/bin/python -m pytest -q`.
- **Paridade com o LAB:** o replay de 3 meses (2026-08..2026-10) com os limites do Shield
  desligados dá **exatamente** os mesmos trades do backtest S16 (entrada, stop, saída, motivo,
  funding, retorno líquido e tamanho). Com o `config.yaml` real, todos os trades do bot são trades
  do LAB; os que faltam têm um bloqueio do Shield registado. Replay de 6 meses (2026-05..2026-10,
  config real, HALT apagado um minuto depois): 60 trades do bot = 60 trades do LAB idênticos; 16
  trades do LAB não feitos (10 abaixo do mínimo nocional ESTIMATIVA, sobretudo BTC; 6 por já haver
  3 posições). Estes meses fazem parte do período fora da amostra usado para escolher o S16: não
  são uma previsão.
- Diferenças conhecidas face ao LAB (não acontecem no S16, que só usa ordens a mercado):
  uma vela com zero trades só se conhece no fecho; com várias ordens limite sobrepostas na mesma
  moeda o bot fica com a primeira a ser executada. Depois da primeira leitura ao vivo do
  `exchangeInfo`, a quantidade é arredondada para baixo ao `stepSize` e o stop ao `tickSize`
  (o LAB não arredonda), o que muda ligeiramente tamanhos e P&L.
- Intervalo de funding: a API pública não o dá; é inferido como o menor entre o tempo desde o
  liquidação anterior e o tempo até à seguinte (`premiumIndex.nextFundingTime`). Coincide com o
  arquivo em todas as liquidações exceto 68 de 525 822 (0,013%), todas em mudanças de intervalo.

### Partes NÃO testadas contra a Binance ao vivo

Esta máquina de desenvolvimento recebe HTTP 451 da Binance, por isso **nada foi testado contra
os servidores reais**. Tudo o resto foi testado com o arquivo (replay) ou com respostas
simuladas:

| parte | como foi testada | o que falta verificar ao vivo |
|---|---|---|
| `RestDataSource` (HTTP GET a `fapi.binance.com`) | sessão HTTP falsa: repetições, backoff, 429/418 `Retry-After`, 451, erros 4xx/5xx, limite de peso | ligação real, certificados, proxies, tempos de resposta |
| `/fapi/v1/klines` | formato de linha documentado; replay no mesmo formato | que a vela em formação vem na resposta logo após a hora |
| `/fapi/v1/fundingRate` | formato documentado; replay do arquivo | atraso entre a liquidação e o aparecimento na API; `fundingTime` exato |
| `/fapi/v1/premiumIndex` (`nextFundingTime`) | replay (próxima liquidação do arquivo) | valores reais e mudanças de intervalo anunciadas |
| `/fapi/v1/exchangeInfo` filtros (`MIN_NOTIONAL`, `LOT_SIZE`, `MARKET_LOT_SIZE`, `PRICE_FILTER`) | exemplo escrito à mão no formato documentado; no replay não há filtros (ficam as ESTIMATIVAS) | valores reais; substituição das estimativas; arredondamentos |
| `exchangeInfo` `status`/`contractType`/`onboardDate` para o universo | replay (símbolos com trades nos últimos 2 dias) | moedas novas classificadas pela impressão digital de TradFi |
| cabeçalho `X-MBX-USED-WEIGHT-1M` | teste unitário | valores reais |
| `run_bot.ps1` e Agendador de Tarefas | não testados (não há Windows/PowerShell aqui); o `run_bot.sh` equivalente é testado | arranque no Windows, reinício após falha |
| `open_dashboard.bat` | não testado (Windows) | duplo clique abre o navegador |
| suspensão real do PC | simulada no replay (salto de relógio de 10 h) | comportamento do `time.sleep` do Windows após suspensão (o bot dorme em blocos de 60 s e relê o relógio) |


## Notificações no telemóvel (Telegram, opcional)

O bot pode mandar-te uma mensagem no Telegram quando abre ou fecha uma trade (simulação) e quando o Shield o para ou retoma.

1. No Telegram, fala com **@BotFather**, envia `/newbot` e segue os passos. Recebes um **token**.
2. Copia `telegram.example.yaml` para `telegram.yaml` e cola o token em `bot_token`. O `telegram.yaml` nunca vai para o GitHub. Não partilhes o token com ninguém, nem em chats.
3. Abre o teu bot no Telegram e carrega em **Start**.
4. Corre `.\.venv\Scripts\python.exe -m bot.notify --find-chat` e cola o número em `chat_id`.
5. Corre `.\.venv\Scripts\python.exe -m bot.notify --test`. Deves receber uma mensagem.
6. Reinicia o bot: fecha a janela do `run_bot.ps1` e arranca outra vez. No arranque aparece `Telegram notifications ON`.

Se o Telegram falhar, o bot continua a negociar normalmente; a falha fica só registada em `logs/bot.log`.

**Segurança:** o único ficheiro do bot autorizado a enviar dados para fora é `bot/notify.py`, e só para `api.telegram.org`. Os testes `test_no_code_path_can_send_orders_or_use_keys` e `test_post_exception_only_reaches_telegram` garantem isso.

## Segundo bot: S02, SÓ PARA OBSERVAR (não aprovado)

O S02 (rompimento do máximo/mínimo de 5 dias, compra **e** aposta na queda) corre como um **segundo bot**, ao lado do S16, com dinheiro de papel, registos e HALT próprios (`logs_s02/`, `state_s02.json`, `HALT_s02`). Serve para veres o bot a trabalhar em mercados de queda e aprender. **Não passou nos testes do LAB** — 68% do lucro veio de uma moeda num mês, o 2.º ano deu −0,05R por trade, as vendas a descoberto perderam dinheiro e a queda máxima foi 63%. Nunca deve ir para dinheiro real.

Numa **segunda** janela do PowerShell (a do S16 continua aberta):
```powershell
powershell -ExecutionPolicy Bypass -File .\run_bot.ps1 --config config.s02.yaml
```
Painel próprio, noutra janela:
```powershell
.\.venv\Scripts\python.exe -m bot.dashboard --config config.s02.yaml --out dashboard_s02.html --watch
```
Relatório: `.\.venv\Scripts\python.exe -m bot.report --days 30 --config config.s02.yaml`. Retomar depois de um HALT: `Remove-Item .\HALT_s02`. As mensagens do Telegram deste bot começam por `[S02 · SÓ OBSERVAÇÃO, NÃO APROVADO]`.
