/ QUANT KDB-X schema and analytical layer.
/ Single primary q source file. Schema version is explicit and auditable.
/ This process is a data/analytics server only: it has no broker, risk or
/ order-submission capability.
/ IPC is bound by the launcher to loopback (see `quant db start`).

/ Runtime identity handshake. An isolated test process receives a unique id on
/ its command line; clients must verify this before issuing write operations.
.quant.cliArg:{[name]
  hits:where .z.x ~\: name;
  if[0=count hits; :""];
  i:hits[0];
  if[i+1>=count .z.x; :""];
  :.z.x[i+1]}

.quant.instanceId:.quant.cliArg["-instanceId"]
.quant.instanceJson:{[]
  .j.j `instanceId`schemaVersion!(.quant.instanceId;.quant.schemaVersion)}

.quant.version:1
.quant.schemaVersion:1

/ ---------------------------------------------------------------------------
/ Canonical tables. Timestamps are q `timestamp` nanoseconds since 2000-01-01;
/ C++ converts from Unix epoch milliseconds before publication. Prices and
/ volumes are floats for analytical use; authoritative money remains fixed
/ point integer paise in C++ and is never recomputed here.
/ ---------------------------------------------------------------------------
quotes:([]seq:`long$();eventId:`symbol$();symbol:`symbol$();source:`symbol$();
  exchangeNs:`long$();providerNs:`long$();lastTradeNs:`long$();
  recvNs:`long$();
  lastPx:`float$();bid:`float$();ask:`float$();volume:`float$();
  quality:`symbol$();outOfOrder:`boolean$())

trades:([]seq:`long$();symbol:`symbol$();source:`symbol$();exchangeNs:`long$();
  price:`float$();size:`float$();side:`symbol$())

depth:([]seq:`long$();symbol:`symbol$();exchangeNs:`long$();
  bid:`float$();bidQty:`float$();ask:`float$();askQty:`float$())

bars:([date:`date$();symbol:`symbol$()]
  open:`float$();high:`float$();low:`float$();close:`float$();
  volume:`float$();dataVintage:`long$())

instruments:([]symbol:`symbol$();isin:`symbol$();exchange:`symbol$();
  eligible:`boolean$();asOf:`date$())

marketStatus:([]ts:`timestamp$();exchange:`symbol$();status:`symbol$())

signals:([]date:`date$();symbol:`symbol$();score:`float$();strategy:`symbol$();
  sourceSchema:`int$())

features:([]date:`date$();symbol:`symbol$();ret20:`float$();ret60:`float$();
  ret120:`float$();vol20:`float$();volSurprise:`float$();vwapDev:`float$())

/ ---------------------------------------------------------------------------
/ Ingestion state. `seen` enforces idempotency by event sequence; maxSeq
/ allows out-of-order events to be retained and flagged rather than silently
/ reordered.
/ ---------------------------------------------------------------------------
.quant.seen:([eventKey:`symbol$()] flag:`boolean$())
.quant.eventKey:{[src;seq] `$((string src),":",string seq)}
.quant.maxSeq:0
.quant.duplicates:0
.quant.outOfOrder:0
.quant.generation:0

.quant.reset:{[]
  quotes::0#quotes; trades::0#trades; depth::0#depth; bars::0#bars;
  instruments::0#instruments; marketStatus::0#marketStatus;
  signals::0#signals; features::0#features;
  .quant.seen:([eventKey:`symbol$()] flag:`boolean$());
  .quant.maxSeq:0; .quant.duplicates:0; .quant.outOfOrder:0;
  .quant.generation:0;
  `reset}

/ Callers send q symbols (C++ ks(); Python pykx.SymbolAtom).
.quant.asSymbol:{[v] v}

.quant.ingest:{[x]
  seq:"j"$x 0; eid:.quant.asSymbol[x 1]; sym:.quant.asSymbol[x 2];
  src:.quant.asSymbol[x 3];
  ets:"j"$x 4; pts:"j"$x 5; lts:"j"$x 6; rns:"j"$x 7;
  px:"f"$x 8; bid:"f"$x 9; ask:"f"$x 10; vol:"f"$x 11;
  qual:.quant.asSymbol[x 12];
  ekey:.quant.eventKey[src;seq];
  if[ekey in key .quant.seen;
     .quant.duplicates::.quant.duplicates+1;
     :`duplicate];
  ooo:seq<.quant.maxSeq;
  if[ooo; .quant.outOfOrder::.quant.outOfOrder+1];
  if[not ooo; .quant.maxSeq::seq];
  .quant.seen[ekey]:1b;
  quotes::quotes upsert ([]seq:enlist seq;eventId:enlist eid;symbol:enlist sym;
    source:enlist src;exchangeNs:enlist ets;providerNs:enlist pts;
    lastTradeNs:enlist lts;recvNs:enlist rns;lastPx:enlist px;
    bid:enlist bid;ask:enlist ask;
    volume:enlist vol;quality:enlist qual;outOfOrder:enlist ooo);
  `ok}

.quant.ingestBar:{[x]
  d0:2000.01.01 + "j"$x 0;
  sym:.quant.asSymbol[x 1];
  o:"f"$x 2; h:"f"$x 3; l:"f"$x 4; c:"f"$x 5; v:"f"$x 6;
  bars::bars upsert ([]date:enlist d0;symbol:enlist sym;open:enlist o;
    high:enlist h;low:enlist l;close:enlist c;volume:enlist v);
  `ok}

.quant.ingestStatus:{[ns;ex;st]
  marketStatus::marketStatus upsert
    ([]ts:enlist `timestamp$ns;
     exchange:enlist ex;status:enlist st);
  `ok}


/ Versioned single-payload write interface used by the C++ client. JSON is
/ parsed with q's .j.k; all canonical fields remain explicit and auditable.
.quant.ingestJson:{[s]
  d:.j.k s;
  .quant.ingest[((d`seq); `$d`eventId; `$d`symbol; `$d`source;
    (d`exchangeNs); (d`providerNs); (d`lastTradeNs); (d`recvNs);
    (d`last); (d`bid); (d`ask); (d`volume); `$d`quality)]}

.quant.ingestBarJson:{[s]
  d:.j.k s;
  .quant.ingestBar[((d`date); `$d`symbol; (d`open); (d`high);
    (d`low); (d`close); (d`volume))]}

/ ---------------------------------------------------------------------------
/ Auditable query surface. Only these functions are exposed over IPC; no
/ arbitrary q source execution is permitted by the C++/Python clients.
/ ---------------------------------------------------------------------------
.quant.counts:{[]
  `quotes`trades`depth`bars`instruments`marketStatus`signals`features!
    (count quotes;count trades;count depth;count bars;count instruments;
     count marketStatus;count signals;count features)}

.quant.state:{[]
  `schemaVersion`generation`maxSeq`duplicates`outOfOrder!(
    .quant.schemaVersion;.quant.generation;.quant.maxSeq;
    .quant.duplicates;.quant.outOfOrder)}

.quant.quotesBySymbol:{[sym] select from quotes where symbol=sym}

.quant.quoteRange:{[lo;hi] select from quotes where seq>=lo,seq<=hi}

/ Point-in-time feature contract (version 2).
/ Returns are P_t/P_(t-n)-1 using sessions available at the decision time.
/ vol20 is a 20-observation rolling population deviation
/ of daily simple returns.
/ Both sides of the as-of join are sorted by symbol then feature timestamp.
/ vwapDev is a daily OHLCV volume-weighted typical-price proxy,
/ not intraday VWAP.
/ A feature computed from session D is available only at the next exchange
/ session boundary, taken from the next bar in the instrument's own series.
.quant.safeRet:{[c;n;k]
  if[n<=k; :0f];
  base:c[(n-1)-k];
  if[base<=0; :0f];
  :((c[n-1])%base)-1}

.quant.featureAt:{[c;v;tp;dates;i;sym]
  cp:c[til i+1]; vp:v[til i+1]; n:i+1;
  r:1_((cp%prev cp)-1);
  avgv:$[n>=20; last mavg[20;vp]; 0f];
  tpv:tp[til i+1];
  vwv:$[n>=20; last mavg[20;tpv*vp]; 0f];
  vw:$[avgv>0; vwv%avgv; cp[n-1]];
  avail:$[n<count dates; dates[n]; dates[n-1]+1];
  featureTs:`timestamp$((`long$avail)*86400000000000);
  keys1:`date`symbol`featureTs`ret20`ret60`ret120;
  keys2:`vol20`volSurprise`vwapDev`dailyVwapProxyDev;
  (keys1,keys2)!
    (dates[n-1]; sym; featureTs; .quant.safeRet[cp;n;20];
     .quant.safeRet[cp;n;60]; .quant.safeRet[cp;n;120];
     $[n>=21; last mdev[20;r]; $[count r>0; dev r; 0f]];
     $[avgv>0; ((last vp)%avgv)-1; 0f];
     $[vw>0; ((last cp)%vw)-1; 0f];
     $[vw>0; ((last cp)%vw)-1; 0f])}

.quant.symbolFeatureHistory:{[sym]
  b:select from bars where symbol=sym;
  n:count b;
  if[n<2; :()];
  rows:(); i:0;
  do[n; rows:rows,enlist .quant.featureAt[exec close from b;exec volume from b;
        ((exec high from b)+(exec low from b)+(exec close from b))%3;
        exec date from b;i;sym];
      i:i+1];
  :rows}

.quant.featureHistory:{[]
  rows:raze .quant.symbolFeatureHistory each distinct (key bars)`symbol;
  if[0=count rows; :()];
  :raze {[row]
    ([]date:enlist row`date; symbol:enlist row`symbol;
      featureTs:enlist row`featureTs; ret20:enlist row`ret20;
      ret60:enlist row`ret60; ret120:enlist row`ret120;
      vol20:enlist row`vol20; volSurprise:enlist row`volSurprise;
      vwapDev:enlist row`vwapDev;
      dailyVwapProxyDev:enlist row`dailyVwapProxyDev)} each rows}

.quant.latestForSymbol:{[h;sym]
  s:select from h where symbol=sym;
  :select from s where featureTs=max featureTs}

.quant.featureTable:{[]
  h:.quant.featureHistory[];
  if[0=count h; :()];
  :raze .quant.latestForSymbol[h;] each distinct h`symbol}

.quant.asofFeatures:{[]
  quotes2:`symbol`featureTs xasc
    select seq,symbol,featureTs:`timestamp$exchangeNs,lastPx from quotes;
  h:.quant.featureHistory[];
  if[0=count quotes2; :`no_data];
  if[0=count h; :`no_data];
  h:`symbol`featureTs xasc h;
  :aj[`symbol`featureTs; quotes2; h]}

/ Breadth: fraction of eligible symbols with positive 20-session return.
.quant.breadth:{[]
  f:.quant.featureTable[];
  if[0=count f; :`no_data];
  (`long$sum f`ret20>0)%count f}

/ ---------------------------------------------------------------------------
/ Historical persistence: date-partitioned on reconciled quote date.
/ HDB path is supplied by the caller; all writes are explicit and auditable.
/ ---------------------------------------------------------------------------
.quant.pathString:{[v] $[10h=type v; v; string v]}
.quant.filePath:{[dir;name] hsym `$((string dir),"/",string name)}

/ Content digest over q's canonical binary encoding. This is a non-
/ cryptographic corruption detector, not tamper authentication.
.quant.digest:{[x] {[h;c] h+((`long$c)*2654435761)}/[0; -8!x]}
.quant.generationDir:{[p;g] `$((.quant.pathString p),"/gen-",string g)}
.quant.currentGeneration:{[p]
  pointer:.quant.filePath[hsym `$(.quant.pathString p);`current];
  if[0=count key pointer; :0];
  :get pointer}

/ Each generation is immutable. The small current pointer is written last,
/ so an interrupted write leaves the previous committed generation intact.
.quant.persist:{[path]
  p:.quant.pathString path;
  system "mkdir -p ",p;
  .quant.generation::.quant.generation+1;
  gen:.quant.generation;
  gdir:.quant.generationDir[p;gen];
  system "mkdir -p ",string gdir;
  root:hsym gdir;
  savedQuotes:quotes; savedBars:bars;
  savedFeatures:.quant.featureHistory[]; savedSeen:.quant.seen;
  savedState:.quant.state[];
  (.quant.filePath[root;`quotes]) set savedQuotes;
  (.quant.filePath[root;`bars]) set savedBars;
  (.quant.filePath[root;`features]) set savedFeatures;
  (.quant.filePath[root;`seen]) set savedSeen;
  (.quant.filePath[root;`state]) set savedState;
  (.quant.filePath[root;`meta]) set (savedState;.quant.counts[]);
  components:`quotes`bars`features`seen`state!(
    (count savedQuotes;.quant.digest savedQuotes);
    (count savedBars;.quant.digest savedBars);
    (count savedFeatures;.quant.digest savedFeatures);
    (count savedSeen;.quant.digest savedSeen);
    (count savedState;.quant.digest savedState));
  manifest:`schemaVersion`generation`components!(
    .quant.schemaVersion;gen;components);
  (.quant.filePath[root;`manifest]) set manifest;
  (.quant.filePath[hsym `$p;`current]) set gen;
  `persisted}

.quant.loadHistorical:{[path]
  gen:.quant.currentGeneration[path];
  if[gen<=0; :`invalid_checkpoint];
  root:hsym .quant.generationDir[path;gen];
  hQuotes:get .quant.filePath[root;`quotes];
  hBars:get .quant.filePath[root;`bars];
  hFeatures:get .quant.filePath[root;`features];
  hMeta:get .quant.filePath[root;`meta];
  hSeen:get .quant.filePath[root;`seen];
  hState:get .quant.filePath[root;`state];
  `quotes`bars`features`seen`state`meta!
    (hQuotes;hBars;hFeatures;hSeen;hState;hMeta)}

.quant.recover:{[path]
  p:.quant.pathString path;
  gen:.quant.currentGeneration[p];
  if[gen<=0; :`invalid_checkpoint];
  root:hsym .quant.generationDir[p;gen];
  manifestPath:.quant.filePath[root;`manifest];
  if[0=count key manifestPath; :`invalid_checkpoint];
  hManifest:get manifestPath;
  manifestSchema:hManifest`schemaVersion;
  manifestGeneration:hManifest`generation;
  if[manifestSchema<>.quant.schemaVersion; :`invalid_checkpoint];
  if[manifestGeneration<>gen; :`invalid_checkpoint];
  components:hManifest`components;
  expectedQuotes:components`quotes; expectedBars:components`bars;
  expectedFeatures:components`features; expectedSeen:components`seen;
  expectedState:components`state;
  hQuotes:get .quant.filePath[root;`quotes];
  hBars:get .quant.filePath[root;`bars];
  hFeatures:get .quant.filePath[root;`features];
  hSeen:get .quant.filePath[root;`seen];
  hState:get .quant.filePath[root;`state];
  if[not (count hQuotes;.quant.digest hQuotes)~expectedQuotes;
     :`invalid_checkpoint];
  if[not (count hBars;.quant.digest hBars)~expectedBars;
     :`invalid_checkpoint];
  if[not (count hFeatures;.quant.digest hFeatures)~expectedFeatures;
     :`invalid_checkpoint];
  if[not (count hSeen;.quant.digest hSeen)~expectedSeen;
     :`invalid_checkpoint];
  if[not (count hState;.quant.digest hState)~expectedState;
     :`invalid_checkpoint];
  quotes::hQuotes; bars::hBars; features::hFeatures; .quant.seen::hSeen;
  .quant.maxSeq::hState`maxSeq;
  .quant.duplicates::hState`duplicates;
  .quant.outOfOrder::hState`outOfOrder;
  .quant.generation::hState`generation;
  .quant.recoveryWatermark::.quant.maxSeq;
  `recovered}

.quant.recoverJson:{[path] .j.j .quant.recover[path]}

.quant.dbStatus:{[]
  `version`schemaVersion`tables!(".quant.version";.quant.schemaVersion;
    key .quant.counts[])}

/ JSON-encoded query surface: stable, typed string results over IPC. Using
/ .j.j here avoids client-side q type decoding and keeps conversions auditable.
.quant.countsJson:{[] .j.j .quant.counts[]}
.quant.stateJson:{[] .j.j .quant.state[]}
.quant.featuresJson:{[] .j.j .quant.featureTable[]}
.quant.dbStatusJson:{[] .j.j .quant.dbStatus[]}
.quant.historicalJson:{[p] .j.j .quant.loadHistorical[p]}

/ Load-time verification marker used by integration tests.
.quant.loaded:1b
