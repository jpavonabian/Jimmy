# Simple Autoreply

Modo de operación opcional de Jimmy: llamas CQ y/o cazas CQ ajenos, y Jimmy trabaja
la cola solo, sin que tengas que pulsar `Alt+N` en cada estación.

Está **apagado por defecto**. Con el modo apagado Jimmy se comporta exactamente igual
que siempre, y un `Jimmy.ini` que no conozca estas claves carga con el modo desactivado.

---

## Por qué existe

La ruta normal de admisión de Jimmy (`WsjtxClient.CallQueue.AddSelectedCall`) está
construida alrededor del trabajo por diplomas: DXCC nuevo, WAS, CQ dirigido, orden de
ranking. Eso es exactamente lo que quieres si vas a por premios, y exactamente lo que
estorba si solo quieres llamar CQ y trabajar a quien conteste.

Simple Autoreply no añade excepciones a esa pila: la puentea. Con el modo activo, la
admisión la decide `AutoReplyFilter.Accepts()` en lugar del `switch` de categorías.

---

## Dónde está

`Alt+O` → pestaña **Simple Autoreply** (la tercera, justo detrás de "Receive / Auto Reply",
que es la que sustituye).

---

## Las opciones, una a una

### Enable Simple Autoreply

El interruptor maestro. Apagado, nada de lo que sigue se consulta.

Encendido, **reemplaza** los ajustes de las secciones Calling, Replying y los Call
Filters. No se suman: se sustituyen.

### Reply to stations answering my CQ

Gobierna la ruta `TO_MYCALL`: los decodes dirigidos a tu indicativo.

Desmarcada, Jimmy deja de encolar a quien te contesta. **Nunca** se aplica a un QSO ya
en marcha ni a un 73/RR73: un contacto empezado siempre termina.

### Reply to stations calling CQ

Decide **qué entra en la cola**, no a quién se llama.

- **Marcada**: entra también el tráfico que no va dirigido a ti, es decir, estaciones
  llamando CQ.
- **Desmarcada**: la cola solo contiene gente que te llama a ti.

> Cuidado con esta: es la que más cambia lo que ves en los cuadros de ciclo 1 y ciclo 2.
> Desmarcada, la mayoría de los decodes de la banda se descartan de entrada.

**No** controla la selección automática. Lo que llegue a la cola se llama, esté esta
casilla como esté. (Confundir ambas cosas fue un bug real: con la casilla apagada, quien
te llamaba se quedaba en la lista marcado "to you" sin que nadie le llamara.)

### Ignore signals weaker than [ N ] dB

Suelo de señal propio del modo. Apagado, la señal no se filtra en absoluto — y el suelo
de la pestaña Receive tampoco se aplica, porque en este modo manda este.

Rechaza estrictamente por debajo: una estación justo en el valor entra.

### Only stations not yet worked on this band

Apagada por defecto: "responder a todos" incluye repetir contactos.

Marcada, se rechaza a quien ya trabajaste en la banda en uso.

### Skip directed CQs not meant for me

Marcada por defecto. No es una preferencia, es operar bien: contestar a un `CQ JA` desde
Europa está feo.

Se aceptan:

| Decode | ¿Entra? |
|---|---|
| `CQ EA1ABC IN80` | sí — CQ sin dirigir |
| `CQ DX K1JT EM51` | sí, si eres DX para él |
| `CQ EU ...` | sí, si `myContinent` es EU |
| `CQ POTA ...` | solo si "POTA" está en la caja de Directed CQ Alert |
| `CQ JA ...` desde Europa | no |

El reconocimiento es **posicional**: `WsjtxMessage.DirectedTo()` solo considera dirigida
la palabra que va detrás de `CQ` si es **solo letras** o **solo dígitos**. Como todo
indicativo lleva al menos un dígito, `CQ JA1ABC IN80` nunca se confunde con `CQ JA`.

### Only DXCC entities I still need [ alcance ]

Apagada por defecto. Marcada, Jimmy solo llama a entidades que te faltan.

| Alcance | Admite |
|---|---|
| **New on any band** | entidad nunca trabajada |
| **New on this band** | entidad no trabajada en la banda en uso |
| **Not confirmed yet** | entidad **sin confirmar**: nunca trabajada, **o** trabajada sin QSL |

Los datos vienen de dos sitios distintos:

- *nueva o no* lo manda **WSJT-X** dentro del decode, contra su propio log.
- *confirmada o no* sale del **logbook de Jimmy**, y solo cuentan **LoTW o QRZ**. Ni
  papel, ni eQSL, ni Club Log.

Por eso "DXCC Unconf" en la lista de estaciones y "New DXCC" no son lo mismo: una
entidad trabajada pero sin confirmar **no** es nueva, así que con los dos primeros
alcances quedaría fuera. Para cazar confirmaciones que te faltan, usa el tercero.

**No se aplica a quien te llama a ti.** Rechazar a alguien que ya te está llamando porque
su entidad está en el log sería feo en aire y tira un QSO medio hecho. Este filtro decide
a quién sale Jimmy a llamar, no a quién contesta.

### Stations: [ Only these / Except these ] [ lista ]

Lista libre separada por comas. **Vacía = no se aplica**, en ninguno de los dos modos.

Cada elemento casa si coincide con:

- el **continente** de la estación (`EU`, `NA`, `AF`…)
- el **país** (`Spain`, `Japan`…)
- un **prefijo del indicativo** (`EA`, `VK`, `EA9`…)

Ejemplos: `EU` deja pasar toda Europa. `Except these` + `EA9` deja fuera Ceuta y Melilla.

---

## Qué NO puentea el modo

Estas puertas siguen aplicándose siempre, estén los filtros como estén:

- **Lista de bloqueados** (`exceptCalls`).
- **Ya en cola**: un decode repetido actualiza la entrada, no la duplica.
- **Periodo T/R**: con Advanced Call Layout se aceptan ambos.
- **Tope por periodo** (`maxAutoGenEnqueue`).
- **La estación tiene que estar libre** — ver abajo.

### Libre significa CQ o despedida

Un decode solo hace llamable a una estación si demuestra que está disponible:

| Decode | ¿Llamable? | Por qué |
|---|---|---|
| `CQ EA1ABC IN80` | sí | está llamando ahora |
| `BG8HNC LY3BFH 73` | sí | acaba de terminar, queda libre |
| `BG8HNC LY3BFH RR73` | sí | igual |
| `BG8HNC LY3BFH -16` | **no** | está pasando informe a otro |
| `VK6OP DL6PX JO40` | **no** | contestando a otro |
| `BG8HNC LY3BFH RRR` | **no** | `RRR` es "todo recibido", no despedida |

"Responder a todos" significa no filtrar por premio, país ni categoría. No significa
llamar a alguien que este mismo decode demuestra que está ocupado con otro.

---

## La selección automática

Admitir en la cola no basta: por sí solo no cambia nada, porque **ninguno de los dos
modos de transmisión empieza un QSO por su cuenta**. Call CQ sigue llamando CQ y Listen
espera tu `Alt+N`.

Con el modo activo, Jimmy elige solo la primera de la cola (que va ordenada por ranking,
así que quien te llama a ti pasa delante).

| Modo Tx | Reply to CQ callers | Comportamiento |
|---|---|---|
| **Call CQ** | **marcada** | **Las dos cosas**: cola vacía → llama CQ; alguien en cola → lo llama; al terminar vuelve a CQ |
| Call CQ | desmarcada | Llama CQ y trabaja a quien conteste |
| Listen | marcada | Solo caza: trabaja la cola sin parar, nunca lanza CQ |
| Listen | desmarcada | Casi parado: no llama CQ, así que nadie te llama |

La selección **no** se dispara si: hay un QSO en marcha, tienes Hold puesto (`Alt+X`),
el CQ está pausado, hay un ajuste de frecuencia en curso, o se está transmitiendo.

En modo Call CQ, si un filtro rechaza a alguien que te llamó, Jimmy **reemite el CQ**.
Sacarlo de su cola no basta: `SetupCq` deja a WSJT-X en "CQ, auto, call 1st" y su
auto-secuencia le contestaría igual.

---

## Ajustes de fuera del modo que te van a afectar

Simple Autoreply no toca la lógica de transmisión. Estos dos siguen mandando, y están
en la pestaña **Transmit**:

### Optimize throughput + Limit Tx repeats

`Limit Tx repeats` es tu máximo de transmisiones a la misma estación. Con **Optimize
throughput** marcada, ese máximo se recorta según la cola:

| Estaciones esperando | Factor | Con límite 3 |
|---|---|---|
| 0–1 | 100 % | 3 intentos |
| 2 | 75 % | 2 |
| 3 | 50 % | 1 |
| 4 o más | 33 % | **1** |

Como la selección automática mantiene la cola llena de forma permanente, Optimize se
queda clavado en su ajuste más agresivo: **un solo intento por estación**. Si te parece
poco, desmarca Optimize o sube el límite.

### Qué pasa cuando una estación expira

Sale de la cola, pero **no para siempre**:

- El contador de timeouts es por indicativo (`timeoutCallDict`).
- Un decode nuevo la vuelve a admitir mientras el contador esté por debajo del máximo
  (entre 3 y 7 según `maxTxRepeat`).
- El contador **se pone a cero** cada vez que Jimmy la vuelve a seleccionar.

Lo único permanente es la lista de bloqueados. La purga por antigüedad de la cola
(`maxCallQueueAgePeriods`, 16 periodos ≈ 4 minutos) tampoco es un veto: un decode nuevo
la reingresa.

---

## Claves de INI

Todas en `%LOCALAPPDATA%\Jimmy\Jimmy.ini`, sección `[Jimmy]`. Ausentes = el valor por
defecto de la tabla, que en conjunto equivale al modo apagado.

| Clave | Valores | Por defecto |
|---|---|---|
| `autoReplySimpleEnabled` | True / False | False |
| `autoReplySimpleMyCallers` | True / False | True |
| `autoReplySimpleCqCallers` | True / False | False |
| `autoReplySimpleMinSnrEnabled` | True / False | False |
| `autoReplySimpleMinSnr` | −30 a 30 | −24 |
| `autoReplySimpleNewOnly` | True / False | False |
| `autoReplySimpleNewDxccOnly` | True / False | False |
| `autoReplySimpleNewDxccScope` | ANY_BAND / CURRENT_BAND / NEW_OR_UNCONFIRMED | ANY_BAND |
| `autoReplySimpleExcludeDirCq` | True / False | True |
| `autoReplySimpleListMode` | ALLOW / EXCLUDE | ALLOW |
| `autoReplySimpleList` | lista separada por comas | vacía |

---

## Diagnóstico

Marca el log de diagnóstico en `Alt+O` → **UDP / Connection**. El fichero sale en
`%LOCALAPPDATA%\Jimmy\log_<fecha>.txt`.

| Buscar | Significa |
|---|---|
| `Simple Autoreply: auto-selecting` | eligió una estación de la cola por su cuenta |
| `re-issuing CQ over filtered caller` | rechazó a quien te llamó y reemitió CQ |
| `autoreply:<motivo>` | motivo del rechazo en la admisión |

Motivos posibles: `directed CQ not for us`, `station busy (not a CQ or sign-off)`,
`snr N < M`, `already worked on band`, `DXCC already worked`,
`DXCC already worked on this band`, `DXCC already confirmed`, `not in allow list`,
`in exclude list`.

El rechazo de "no respondo a quien llama CQ" solo se escribe con `debug=True` en el INI.
Si ves pocas estaciones y ningún motivo en el log, es probablemente ese.

---

## Tests

```
test.bat                          unitarios (incluye AutoReplyFilter*)
run_replay_tests.bat              suite de replay original
run_autoreply_replay_tests.bat    16 escenarios de Simple Autoreply
```

Ambas suites de replay usan un `Jimmy.ini` desechable (`JIMMY_TEST_INI_PATH`) construido
desde el tuyo, que solo se **lee**. Las claves que las aserciones dan por supuestas están
fijadas en `JimmyReplaySeed.py`, para que el resultado no dependa de cómo tengas tú
configurado el programa.

La suite de Simple Autoreply arranca y para su propia instancia de Jimmy en cada
escenario, así que hay que cerrar Jimmy y WSJT-X antes de lanzarla.
