# CS2 AI Coach — pipeline visual inicial

Captura de tela ou leitura de vídeo → recortes do HUD → OCR → confirmação temporal → JSONL.

Esta versão observa vida, munição, cronômetro, pontos de colete (`armor`, de 0 a 100), placar e contadores de jogadores vivos por equipe. Os recortes em `config/hud.example.json` foram calibrados no HUD de 1280×960 fornecido e precisam de ajuste para outros layouts. A opção `--killfeed` habilita o feed de eliminações, incluindo mensagens sem destaque local. A configuração padrão também identifica o ícone de C4 plantada. Ainda não reconhece jogadores na cena, explosão, defuse ou fases da rodada. Mudanças de HUD são observações; uma queda de munição não prova, sozinha, que houve um disparo.

A leitura de colete usa o número pequeno dentro do escudo à esquerda da vida. Ela emite `hud.initial` e `hud.changed` com `field: "armor"`, seguindo a mesma confirmação temporal dos demais campos. O recorte foi validado no print com colete 100 e na captura salva com colete 76; mudanças precisam de validação ao vivo. A ausência do escudo ou uma leitura inválida retorna `null` no diagnóstico e não é convertida em zero; o último valor confirmado permanece no tracker. Esta versão não identifica capacete.

## Executar no Windows

Requisitos: Python 3.10+ e Tesseract OCR instalado separadamente e disponível no PATH (ou indicado por `--tesseract-cmd`).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\cs2-events.exe --monitor 1 --output events.jsonl
.\.venv\Scripts\cs2-events.exe --video recordings/partida.mp4 --output events.jsonl
```

Sem `--output`, os eventos vão para stdout. Ctrl+C encerra a captura. O arquivo de saída é sobrescrito a cada execução. `--sample-fps 5` define a frequência desejada; a captura com OCR pode ser mais lenta dependendo do computador.

## Calibrar

Cada `rect` usa coordenadas normalizadas `[x, y, largura, altura]`, entre 0 e 1, relativas ao monitor ou quadro do vídeo. Recorte apenas o número desejado; inclua o cronômetro inteiro. Use um arquivo próprio com `--config config/hud.json`. A captura considera todo o monitor: o jogo deve ocupar a área correspondente aos recortes.

Se a captura do monitor inclui o jogo 1280×960 no canto superior esquerdo e o desktop ao redor, use `--capture-region 0 0 1280 960`. Os recortes do HUD passam a ser relativos apenas a essa área. Esse argumento recebe pixels relativos ao monitor selecionado e deve ser ajustado se a posição ou o tamanho do jogo mudar.

`confirmation_frames` exige leituras consecutivas iguais antes de emitir um evento. Leituras inválidas interrompem a confirmação, mas preservam o último estado conhecido. Isso reduz ruído e também pode perder mudanças rápidas. Não há score de confiança calibrado nesta versão.

## Contrato de saída

### Formato compacto para coaching (padrão)

`--format coach` é o padrão. O arquivo é JSONL: um cabeçalho, mudanças confirmadas do HUD agrupadas por instante, eventos e um resumo final das detecções. O cabeçalho é enviado uma única vez. Não se repetem caminhos locais, hora de processamento, número do frame, evidências de OCR ou o contexto completo em cada linha. Todos os valores confirmados de vida, colete, munição, cronômetro, placar, vivos e lado são preservados como mudanças, sem amostragem adicional do log.

```json
{"t":0.4,"state":{"hp":100,"armor":100,"score":[3,1],"side":"T"}}
{"t":5.4,"events":[{"type":"kill","id":"killfeed-16","teams":["T","CT"],"seen":5.2}],"unknown":[]}
{"t":26.2,"events":[{"type":"bomb.planted","state":"planted","seen":25.8}],"unknown":["clock"]}
{"t":46.8,"state":{"hp":54,"armor":94}}
```

- `t`: segundos desde o início do vídeo/captura, no instante de confirmação. `seen` guarda a primeira observação dos eventos quando anterior à confirmação.
- `state`: somente valores que mudaram. `hp` é vida; `clock` é o cronômetro observado. `score` e `alive` usam a ordem **CT, T**; zero é válido, `null` significa ainda não confirmado. Os valores são históricos, não garantias de que o HUD permaneceu igual entre registros.
- `events`: `kill`, `death`, `feed_kill`, `feed_update`, `bomb.planted` e `bomb.state`. `teams` usa a ordem **atacante, vítima**, e não CT/T.
- `unknown`: campos do contexto que não estavam confirmados no instante dos eventos. `hp`, `ammo`, `score.CT`, `alive.T`, etc. não devem ser tratados como valores atuais só porque existiram antes. A lista é explícita em cada linha com eventos; `[]` significa que os campos monitorados estavam confirmados. Não representa todos os intervalos de falha entre eventos.
- `feed_update`: atualiza o registro de mesmo `id` com a classificação `as`, sem somar outra eliminação. `preexisting: true` marca notificações já presentes no primeiro frame.
- `detected`: contagens finais corrigidas por `id`. Notificações preexistentes ficam separadas e não entram nos totais de kills/deaths observadas durante o trecho. Os totais são detecções, não estatísticas garantidas da partida; detectores desativados estão indicados no cabeçalho.

Nenhum nickname é configurado. Se o OCR opcional de nomes estiver ativado, textos são preservados como `names_unverified`; os `null` de nomes, plantador e bombsite são omitidos. Não se inventam identidades. Vantagem numérica pode ser calculada a partir de lado/vivos quando confirmados; a estimativa de rodada e outros valores derivados não são repetidos. O agente deve receber o cabeçalho e a sequência desde o estado inicial, ou um estado reconstruído ao trabalhar com trechos. Este formato não chama nem envia dados a um LLM.

Para gerar o compacto e preservar também o log detalhado:

```powershell
.\.venv\Scripts\cs2-events.exe --video "C:\caminho\partida.mp4" --killfeed --output coaching.jsonl --raw-output events-raw.jsonl
```

Para converter um log detalhado existente, sem repetir OCR:

```powershell
.\.venv\Scripts\python.exe -m cs2_coach.coaching --input events-video.jsonl --output coaching.jsonl
Get-Content .\coaching.jsonl
```

O conversor aceita o formato **raw**, não um arquivo já compacto. `end_of_log` marca a última observação do arquivo; não implica que o vídeo terminou naquele instante. Na captura direta, `end` distingue `complete`, `interrupted`, `duration_limit` e `error`. Os arquivos de saída são sobrescritos; escolha outro nome para preservar execuções anteriores.

### Formato detalhado e compatibilidade

Use `--format raw` para manter o contrato anterior (`hud.changed`, `player.kill`, contexto completo e evidências), ou `--raw-output` em paralelo ao compacto. As seções abaixo descrevem esse formato detalhado. Filtros PowerShell baseados em `.type` no topo do registro continuam aplicáveis ao raw; no compacto os eventos estão em `.events` e as mudanças em `.state`.

### Diagnóstico da captura

Adicione `--debug-dir debug --duration 20` ao comando para salvar `debug/frame.png` e os recortes `region-1.png`, `region-2.png`, etc. na ordem da configuração. Volte ao jogo imediatamente e espere 25 segundos antes de voltar ao terminal: a captura encerra sozinha e preserva a última imagem salva. Os diagnósticos ficam nos arquivos; adicione `--verbose` para também imprimir as leituras no terminal a cada aproximadamente dois segundos (`null` indica leitura rejeitada). As imagens são sobrescritas. Elas contêm a tela do monitor; mantenha apenas o jogo visível durante o diagnóstico. Uma captura preta ou da área de trabalho indica que é necessário verificar o modo de exibição ou o monitor selecionado. `--duration` limita o tempo de captura, podendo excedê-lo pelo processamento de um quadro.

```json
{"schema_version":"1.0","sequence":2,"type":"hud.changed","observed_at":"2026-10-06T00:00:00+00:00","timestamp_ms":2400.0,"frame_id":12,"source":{"kind":"screen","monitor":1},"field":"health","previous":100,"value":80,"evidence":{"method":"ocr","consecutive_readings":3}}
```

`timestamp_ms` é relativo ao início da captura ou à posição no vídeo. `observed_at` é a hora UTC de processamento. O primeiro valor confirmado emite `hud.initial`; os próximos valores diferentes emitem `hud.changed`.

## Verificação

### Coleta para o detector de kill feed

Use `--collect-killfeed killfeed-samples --duration 60` para preservar recortes do canto superior direito a cada aproximadamente 0,5 segundo durante um minuto. Os PNGs recebem nomes únicos e não sobrescrevem mensagens anteriores. A região inicial cobre a metade direita entre 6% e 38% da altura. Essa opção apenas coleta exemplos; use `--killfeed` para emitir eventos. Os recortes podem conter nomes de outros jogadores. Não é necessário configurar o nick.

### Detector experimental de eliminações e mortes

`--killfeed` emite `player.kill` para o padrão de borda vermelha e `player.death` para o padrão de fundo vermelho preenchido acompanhado da tela avermelhada de morte. A classificação foi calibrada nos dois prints fornecidos; ainda precisa de validação em sequência ao vivo. Não usa nome fixo nem exige que os nomes sejam separados ou reconhecidos. Por padrão, `attacker_ocr` e `victim_ocr` ficam `null`; `--killfeed-names` habilita OCR auxiliar, mais lento e sujeito a erros. A classificação e a deduplicação não dependem desses textos.

Para investigar falhas ao vivo, combine `--killfeed --debug-dir debug --collect-killfeed killfeed-samples --duration 60`. O arquivo `debug/diagnostics-<execução>.jsonl` preserva as leituras de cada quadro, os contornos encontrados, os retângulos aceitos, rejeições por falta de conteúdo e a detecção da tela de morte. Os recortes em `killfeed-samples` preservam o feed mesmo depois de voltar ao editor. Esses arquivos são locais e não são apagados automaticamente. O detector possui testes com recortes reais e variações de brilho, resolução e cor dos nomes; esses testes não substituem uma gravação real da partida.

Cada linha exige duas detecções consecutivas. Linhas neutras com texto segmentado aguardam também um segundo para permitir que o destaque local apareça. O rastreamento compara a imagem dos caracteres da vítima, sem reconhecer nem configurar o nickname. A posição vertical, assistências e o fundo translúcido não definem a identidade. Após 12 segundos sem detecção, a identidade pode emitir outro evento. Duas linhas idênticas simultâneas são rastreadas separadamente; uma nova morte da mesma vítima durante esse intervalo, como em deathmatch, pode ser suprimida.

Os candidatos precisam coincidir com a margem direita e as linhas do HUD. Notificações neutras exigem um ícone entre os participantes. Quando não é possível segmentar as cores dos nomes, permanece uma assinatura auxiliar dos elementos claros. Os testes incluem a execução real de uma eliminação seguida de morte, uma sequência de terceiros e recortes do vídeo com assistências, mudanças de cenário e bordas enfraquecidas.

O detector pressupõe a perspectiva do jogador com o HUD e as cores dos exemplos. Assistências foram verificadas no vídeo fornecido; teamkills, suicídios, modo espectador, demos, deathmatch e HUDs diferentes ainda precisam de validação. A morte pode ser perdida se a animação avermelhada for curta demais. O detector ainda não lê arma nem headshot. Mensagens já presentes ao iniciar também geram eventos, marcados com `present_at_start: true` quando observadas no primeiro quadro; o timestamp indica quando foram observadas, não o instante exato da eliminação.

Mensagens sem destaque local emitem `killfeed.kill`, com `local_role: null`. Eliminações e mortes locais continuam usando `player.kill` e `player.death`; são três tipos para a mesma categoria de acontecimento, e não é emitido um evento genérico adicional para cada evento local. `attacker_team` e `victim_team` são inferidos das cores do texto (T/CT) quando legíveis. `null` no papel local significa desconhecido, não uma prova de ausência de participação. O teste de terceiros usa a sequência real Bassey → Ulysses e verifica que não vira uma eliminação local.

Cada notificação recebe `event_id`, único dentro da execução, e `classification`. Se uma notificação já emitida como genérica ganhar evidência local depois, o pipeline emite `killfeed.updated` com o **mesmo** `event_id` e a classificação corrigida. O consumidor deve atualizar a classificação desse registro, sem contar outra eliminação. O desaparecimento da borda não rebaixa uma classificação local já observada.

### Plantio da C4

A configuração padrão inclui `bomb_template`, relativo ao arquivo de configuração, e ativa a leitura visual do ícone da C4 na posição do cronômetro. Após observar um cronômetro normal e confirmar o ícone em três frames consecutivos, o pipeline emite `bomb.planted` uma única vez. Oscilações e desaparecimentos breves do ícone não repetem o evento. Um cronômetro normal confirmado rearma o detector para um próximo plantio.

Se o vídeo começar com a bomba já plantada, o evento é `bomb.state`, com `state: "planted"` e `transition_observed: false`; não se afirma que o plantio ocorreu naquele instante. `planter` e `site` permanecem `null`, pois o ícone não identifica jogador nem bombsite. O desaparecimento da C4 não é interpretado como explosão ou defuse. Esses resultados ainda não possuem detectores. Configurações sem `bomb_template` deixam essa leitura desabilitada.

Exemplo parcial:

```json
{"type":"bomb.planted","timestamp_ms":26200.015,"first_observed_ms":25800.015,"state":"planted","transition_observed":true,"planter":null,"site":null,"evidence":{"method":"planted_bomb_icon_template","consecutive_readings":3}}
```

### OCR do placar

Os recortes do placar excluem as bordas do painel e usam `foreground: "bright_text"` para separar os dígitos claros do cenário, em vez de calcular o contraste a partir do fundo translúcido. Uma segunda tentativa com limiar menor recupera traços enfraquecidos pela compressão quando a primeira leitura é rejeitada. Leituras não reconhecidas continuam como `null`; não são substituídas por um placar inventado.

### Contexto dos eventos

Todo evento inclui `context`, com `side`, `score`, `players_alive`, `alive_advantage`, `round_timer`, `health`, `armor` e `ammo`. A vantagem é a quantidade de aliados vivos menos a de adversários, apenas quando lado e contadores estão confirmados. Os contadores são lidos no HUD, não calculados pela subtração das mortes detectadas. O layout atual admite de 0 a 5 jogadores por equipe.

`round_number_estimate` é a soma dos dois placares mais um. É uma estimativa para partidas com pontuação normal: aquecimento, fim de rodada, treino e reinícios manuais podem não corresponder a esse número. Não há identificação de fase de rodada ainda.

`context.observations` separa `confirmed`, `pending`, `unavailable` e `stale`. Valores indisponíveis ficam `null`; o último confirmado aparece explicitamente em `last_confirmed_value`, com seu instante. A ausência visual e uma falha de OCR ainda compartilham `unavailable`. Zero é preservado como um valor válido. `first_observed_ms` indica o primeiro quadro da sequência de confirmação; `timestamp_ms` indica a confirmação. Com `--debug-dir`, cada evento do feed também salva um recorte independente em `evidence.image_path`.

### Reconhecimento do lado

O lado é reconhecido pelo emblema central do HUD, sem nickname. O projeto inclui referências T/TR e CT calibradas nos prints fornecidos; a distinção entre os dois emblemas foi validada nessas imagens. Quando nenhum template corresponde com segurança, `side` fica `null`. Não inferimos CT apenas por não reconhecer T.

Para calibrar CT, salve um print da área do jogo com o emblema CT visível, no mesmo layout, e execute:

```powershell
.\.venv\Scripts\python.exe -m cs2_coach.calibrate_side --image "C:\caminho\ct.png" --side CT
```

Isso salva `config/templates/CT.png`; reinicie a captura. Cada imagem de referência pode ser substituída pela calibração correspondente. Após mudar o layout, valide novamente em uma captura ao vivo.

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
```

Para incluir o OCR real nos testes, defina `TESSERACT_CMD` com o caminho do executável, ou deixe `tesseract` disponível no PATH. Os demais testes visuais não chamam OCR. O pipeline usa somente pixels e não lê memória do jogo.
