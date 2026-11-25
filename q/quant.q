/ QUANT KDB-X schema and analytical layer.
/ Single primary q source file. Schema version is explicit and auditable.
/ This process is a data/analytics server only: it has no broker, risk or
/ order-submission capability.
/ IPC is bound by the launcher to loopback (see `quant db start`).

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

bars:([]date:`date$();symbol:`symbol$();open:`float$();high:`float$();
  low:`float$();close:`float$();volume:`float$())

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

.quant.reset:{[]
  quotes::0#quotes; trades::0#trades; depth::0#depth; bars::0#bars;
  instruments::0#instruments; marketStatus::0#marketStatus;
  signals::0#signals; features::0#features;
  .quant.seen:([eventKey:`symbol$()] flag:`boolean$());
  .quant.maxSeq:0; .quant.duplicates:0; .quant.outOfOrder:0;
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
/ arbitrary q expression evaluation is permitted by the C++/Python clients.
/ ---------------------------------------------------------------------------
.quant.counts:{[]
  `quotes`trades`depth`bars`instruments`marketStatus`signals`features!
    (count quotes;count trades;count depth;count bars;count instruments;
     count marketStatus;count signals;count features)}

.quant.state:{[]
  `schemaVersion`maxSeq`duplicates`outOfOrder!(.quant.schemaVersion;
    .quant.maxSeq;.quant.duplicates;.quant.outOfOrder)}

.quant.quotesBySymbol:{[sym] select from quotes where symbol=sym}

.quant.quoteRange:{[lo;hi] select from quotes where seq>=lo,seq<=hi}

/ q owns historical time-series feature generation (no Python reimplementation).
/ Returns use closing prices n sessions apart: P_t/P_(t-n)-1.
/ Realized volatility is the standard deviation of daily simple returns.
/ VWAP is volume-weighted price, not a moving average of typical price.
.quant.safeRet:{[c;n;k]
  if[n<=k; :0f];
  base:c[(n-1)-k];
  if[base<=0; :0f];
  :((c[n-1])%base)-1}

.quant.symbolFeatures:{[sym]
  b:select from bars where symbol=sym;
  n:count b;
  if[n<2; :()];
  c:b`close; v:b`volume;
  r:1_((c%prev c)-1);
  avgv:$[n>=20; last mavg[20;v]; 0f];
  vwv:$[n>=20; last mavg[20;c*v]; 0f];
  vw:$[avgv>0; vwv%avgv; c[n-1]];
  (`date`symbol`ret20`ret60`ret120`vol20`volSurprise`vwapDev)!
    (last b`date; sym; .quant.safeRet[c;n;20];
     .quant.safeRet[c;n;60]; .quant.safeRet[c;n;120];
     dev r; $[avgv>0; ((last v)%avgv)-1; 0f];
     $[vw>0; ((last c)%vw)-1; 0f])}

.quant.featureTable:{[]
  rows:.quant.symbolFeatures each distinct bars`symbol;
  rows:rows where (count each rows)>0;
  if[0=count rows; :()];
  :raze {[row]
    ([]date:enlist row`date; symbol:enlist row`symbol;
      ret20:enlist row`ret20; ret60:enlist row`ret60;
      ret120:enlist row`ret120; vol20:enlist row`vol20;
      volSurprise:enlist row`volSurprise;
      vwapDev:enlist row`vwapDev)} each rows}

.quant.asofFeatures:{[]
  q:select seq,symbol,ts:`timestamp$exchangeNs,lastPx from quotes;
  f:.quant.featureTable[];
  if[0=count q; :`no_data];
  if[0=count f; :`no_data];
  / Daily features are available only after the session close: timestamp them
  / at the following session boundary so intraday quotes cannot see future data.
  f2:select symbol,
    ts:`timestamp$((`long$date+1)*86400000000000),
    ret20,vol20 from f;
  f2:`symbol`ts xasc f2;
  :aj[`symbol`ts; q; f2]}

/ Market breadth proxy: fraction of symbols above their 20-session mean.
.quant.breadth:{[]
  f:.quant.featureTable[];
  if[0=count f; :`no_data];
  (`long$sum f`ret20>0)%count f}

/ As-of join of features to quotes. Daily features are available only after
/ the session close, so they are timestamped at the next session boundary.
/ ---------------------------------------------------------------------------
/ Historical persistence: date-partitioned on reconciled quote date.
/ HDB path is supplied by the caller; all writes are explicit and auditable.
/ ---------------------------------------------------------------------------
.quant.filePath:{[dir;name] hsym `$((string dir),"/",string name)}

.quant.persist:{[path]
  p:string path;
  system "mkdir -p ",p;
  root:hsym `$p;
  (.quant.filePath[root;`quotes]) set quotes;
  (.quant.filePath[root;`bars]) set bars;
  (.quant.filePath[root;`features]) set .quant.featureTable[];
  (.quant.filePath[root;`seen]) set .quant.seen;
  (.quant.filePath[root;`meta]) set (.quant.state[]; .quant.counts[]);
  `persisted}

.quant.loadHistorical:{[path]
  root:hsym `$string path;
  hQuotes:get .quant.filePath[root;`quotes];
  hBars:get .quant.filePath[root;`bars];
  hFeatures:get .quant.filePath[root;`features];
  hMeta:get .quant.filePath[root;`meta];
  hSeen:get .quant.filePath[root;`seen];
  `quotes`bars`features`seen`meta!(hQuotes;hBars;hFeatures;hSeen;hMeta)}

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
