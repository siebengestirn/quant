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

/ 128-bit content digest for corruption detection. MD5 is well-specified and
/ order-sensitive but NOT collision-resistant and NOT tamper authentication.
.quant.digest:{[x] md5["c"$-8!x]}
.quant.generationDir:{[p;g] `$((.quant.pathString p),"/gen-",string g)}
.quant.currentGeneration:{[p]
  pointer:.quant.filePath[hsym `$(.quant.pathString p);`current];
  if[0=count key pointer; :0];
  :get pointer}
.quant.componentNames:`quotes`bars`features`seen`state`meta

/ One verifier shared by recovery AND ordinary historical loading.
.quant.verifyComponent:{[p;g;acc;name]
  if[not acc; :0b];
  if[not name in .quant.componentNames; :0b];
  root:hsym .quant.generationDir[p;g];
  path:.quant.filePath[root;name];
  if[0=count key path; :0b];
  component:@[get;path;{::}];
  if[component~(::); :0b];
  m:get .quant.filePath[root;`manifest];
  expected:(m`components)[name];
  :((count component);.quant.digest component)~expected}

.quant.verifyGeneration:{[p;g]
  root:hsym .quant.generationDir[p;g];
  manifestPath:.quant.filePath[root;`manifest];
  if[0=count key manifestPath; :0b];
  m:get manifestPath;
  componentNames:key (m`components);
  schema:.quant.schemaVersion;
  manifestGeneration:(m`generation);
  expectedCount:count .quant.componentNames;
  if[(m`schemaVersion)<>schema; :0b];
  if[manifestGeneration<>g; :0b];
  if[(count componentNames)<>expectedCount; :0b];
  :.quant.verifyComponent[p;g]/[1b;.quant.componentNames]}

/ Cooperative cross-process writer lock using an exclusive directory.
.quant.writerAcquire:{[p]
  result:@[system;("mkdir ",p,"/.writer.lock");{0b}];
  0=count result}
.quant.writerRelease:{[p]
  @[system;("rmdir ",p,"/.writer.lock");{::}];
  ::}
.quant.sync:{[] system "sync"; ::}

/ Immutable generation: components are written to a staging directory, synced,
/ then atomically renamed before the current pointer is published.
.quant.persist:{[path]
  p:.quant.pathString path;
  system ("mkdir -p ",p);
  if[not .quant.writerAcquire[p]; :`writer_busy];
  gen:.quant.currentGeneration[p]+1;
  genText:string `long$gen;
  stagedPath:p,"/gen-",genText,".staging";
  finalPath:p,"/gen-",genText;
  stagingDir:`$stagedPath;
  finalDir:`$finalPath;
  existingGenerations:key hsym `$p;
  finalName:`$("gen-",genText);
  if[finalName in existingGenerations;
      .quant.writerRelease[p];
      :`generation_conflict];
  system ("mkdir ",stagedPath);
  root:hsym stagingDir;
  .quant.generation::gen;
  savedQuotes:quotes; savedBars:bars;
  savedFeatures:.quant.featureHistory[]; savedSeen:.quant.seen;
  savedState:.quant.state[];
  savedMeta:(savedState;.quant.counts[]);
  (.quant.filePath[root;`quotes]) set savedQuotes;
  (.quant.filePath[root;`bars]) set savedBars;
  (.quant.filePath[root;`features]) set savedFeatures;
  (.quant.filePath[root;`seen]) set savedSeen;
  (.quant.filePath[root;`state]) set savedState;
  (.quant.filePath[root;`meta]) set savedMeta;
  components:.quant.componentNames!(
    (count savedQuotes;.quant.digest savedQuotes);
    (count savedBars;.quant.digest savedBars);
    (count savedFeatures;.quant.digest savedFeatures);
    (count savedSeen;.quant.digest savedSeen);
    (count savedState;.quant.digest savedState);
    (count savedMeta;.quant.digest savedMeta));
  manifest:`schemaVersion`generation`components!(
    .quant.schemaVersion;gen;components);
  (.quant.filePath[root;`manifest]) set manifest;
  .quant.sync[];
  system ("mv -f ",stagedPath," ",finalPath);
  .quant.sync[];
  (.quant.filePath[hsym `$p;`currentTmp]) set gen;
  system ("mv -f ",p,"/currentTmp ",p,"/current");
  .quant.sync[];
  .quant.writerRelease[p];
  `persisted}

/ Uncommitted staging generations are never promoted; remove them explicitly.
.quant.recoverOrphans:{[p]
  path:.quant.pathString p;
  if[0=count key hsym `$path; :0];
  names:key hsym `$path;
  staged:names where (like[string each names;"*staging*"]);
  .quant.removeStaged[path] each staged;
  count staged}
.quant.removeStaged:{[path;name]
  @[system;("rm -rf ",path,"/",string name);{::}];
  ::}

.quant.loadHistorical:{[path]
  p:.quant.pathString path;
  gen:.quant.currentGeneration[p];
  if[gen<=0; :`invalid_checkpoint];
  if[not .quant.verifyGeneration[p;gen]; :`invalid_checkpoint];
  root:hsym .quant.generationDir[p;gen];
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
  if[not .quant.verifyGeneration[p;gen]; :`invalid_checkpoint];
  root:hsym .quant.generationDir[p;gen];
  hState:get .quant.filePath[root;`state];
  quotes::get .quant.filePath[root;`quotes];
  bars::get .quant.filePath[root;`bars];
  features::get .quant.filePath[root;`features];
  .quant.seen::get .quant.filePath[root;`seen];
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
