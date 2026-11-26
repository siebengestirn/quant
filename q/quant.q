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
.quant.seen:([eventKey:`symbol$()] eventId:`symbol$();digest:())
.quant.eventKey:{[src;seq] `$((string src),":",string seq)}
.quant.maxSeq:0
.quant.maxSeqBySource:()!()
.quant.duplicates:0
.quant.sequenceConflicts:0
.quant.outOfOrder:0
.quant.generation:0

.quant.reset:{[]
  quotes::0#quotes; trades::0#trades; depth::0#depth; bars::0#bars;
  instruments::0#instruments; marketStatus::0#marketStatus;
  signals::0#signals; features::0#features;
  .quant.seen:([eventKey:`symbol$()] eventId:`symbol$();digest:());
  .quant.maxSeq:0; .quant.maxSeqBySource:()!();
  .quant.duplicates:0; .quant.sequenceConflicts:0; .quant.outOfOrder:0;
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
  digest:.quant.digest x;
  if[ekey in key .quant.seen;
      if[(.quant.seen[ekey]`digest)~digest;
          .quant.duplicates::.quant.duplicates+1;
          :`duplicate_identical];
      .quant.sequenceConflicts::.quant.sequenceConflicts+1;
      :`sequence_conflict];
  watermark:$[src in key .quant.maxSeqBySource; .quant.maxSeqBySource[src]; 0];
  ooo:seq<watermark;
  if[ooo; .quant.outOfOrder::.quant.outOfOrder+1;
      .quant.maxSeqBySource[src]::watermark];
  if[not ooo;
      .quant.maxSeqBySource[src]::seq;
      .quant.maxSeq::max (.quant.maxSeq;seq)];
  .quant.seen[ekey]:(`eventId`digest!(eid;digest));
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

/ Canonical path grammar. Only absolute paths with a reviewed character set
/ are accepted. Relative inputs are resolved against the process working
/ directory and dot segments are removed lexically, never by a shell glob.
.quant.pathAlphabet:"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
.quant.pathAlphabet,:"0123456789._:-"
.quant.canonicalPath:{[p]
  s:.quant.pathString p;
  if[not count s; :""];
  if[s like ":*"; s:1_s];
  s:$[s like "/*"; s; (first system "pwd"),"/",s];
  segments:"/" vs s;
  valid:{[x]
    if[0=count x; :0b];
    :all ((.quant.pathAlphabet?x)<count .quant.pathAlphabet)};
  kept:segments where 0<count each segments;
  if[not all (valid each kept); :""];
  if[any ({[x] x ~ ".."} each kept); :""];
  :"/", "/" sv kept}
.quant.safePath:{[p] 0<count .quant.canonicalPath[p]}
.quant.checkedSystem:{[cmd]
  r:@[system;cmd;{`q_system_error}];
  ((type r)=0h) and (0=count r)}
.quant.sync:{[] .quant.checkedSystem["sync"]}

.quant.quotePath:{[p]
  c:.quant.canonicalPath p;
  if[not count c; '`unsafe_path];
  :"'",c,"'"}

/ Strong content identity. SHA-256 replaces the former MD5 digest; the
/ computation is delegated to the checked quant CLI so both languages use
/ the same algorithm. Digests detect corruption but are not authentication.
.quant.quantBin:{[] .quant.cliArg["-quantBin"]}
.quant.fileDigest:{[path]
  tool:.quant.quantBin[];
  if[not count tool; tool:"build/quant"];
  out:@[system;(tool," digest ",.quant.quotePath path);{`q_digest_error}];
  if[not (type out) in (0h;10h); '`digest_failed];
  if[0=count out; '`digest_failed];
  :$[10h=type out; out; first out]}
.quant.digestCounter:0
.quant.digest:{[x]
  tool:.quant.quantBin[];
  if[not count tool; tool:"build/quant"];
  .quant.digestCounter::.quant.digestCounter+1;
  tmp:"/tmp/quant-digest-", (string `long$.z.p), "-",
    string .quant.digestCounter;
  hsym[`$tmp] set .j.j x;
  out:.quant.fileDigest[tmp];
  .quant.checkedSystem[("rm -f ",.quant.quotePath tmp)];
  :out}
.quant.syncFile:{[path]
  tool:.quant.quantBin[];
  if[not count tool; tool:"build/quant"];
  r:@[system;(tool," fsync ",.quant.quotePath path);{`q_fsync_error}];
  :((type r)=0h) and (1=count r) and ((first r) ~ "ok")}
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


/ Cross-process writer lock with a numeric identity record. The owner file is
/ written with q file APIs (never shell interpolation) and contains a bounded
/ positive PID plus a fencing token. A lock without an owner file is an
/ explicit orphan and is never guessed or silently removed by acquire.
.quant.ownerPath:{[p] .quant.canonicalPath[p],"/.writer.lock/owner"}
.quant.validPid:{[text]
  if[not (count text)>0; :0b];
  if[0=count text; :0b];
  if[not all ((("0123456789")?text)<10); :0b];
  pid:"J"$text;
  :(pid>0) and (pid<999999999)}

.quant.lockState:{[p]
  lock:.quant.canonicalPath[p],"/.writer.lock";
  if[not .quant.checkedSystem[("test -d ",(.quant.quotePath lock))]; :`absent];
  owner:.quant.ownerPath p;
  if[not .quant.checkedSystem[("test -f ",
      (.quant.quotePath owner))]; :`orphan];
  record:@[get;hsym `$owner;{::}];
  if[record~(::); :`orphan];
  if[not .quant.validPid[string record`pid]; :`orphan];
  :`owned}

.quant.checkedRemoveLock:{[p]
  lock:.quant.canonicalPath[p],"/.writer.lock";
  if[0=count key hsym `$lock; :1b];
  ok:.quant.checkedSystem[("rm -rf ",.quant.quotePath lock)];
  :ok and (0=count key hsym `$lock)}

.quant.staleLockRecovery:{[p]
  state:.quant.lockState p;
  if[state=`absent; :1b];
  if[state=`orphan; :0b];
  record:get hsym `$(.quant.ownerPath p);
  pid:string record`pid;
  if[not .quant.validPid pid; :0b];
  alive:@[system;("kill -0 ",pid," 2>/dev/null");{::}];
  if[(type alive)=0h; if[0<count alive; :0b]];
  :.quant.checkedRemoveLock p}

.quant.writerAcquire:{[p]
  c:.quant.canonicalPath p;
  if[0=count c; :0b];
  if[not .quant.staleLockRecovery[c]; :0b];
  lock:c,"/.writer.lock";
  created:@[system;("mkdir ",.quant.quotePath lock);{`q_system_error}];
  if[not ((type created)=0h) and (0=count created); :0b];
  owner:.quant.ownerPath c;
  pid:"J"$first system "echo $$";
  ok:.quant.validPid string pid;
  if[not ok; .quant.checkedRemoveLock[c]; :0b];
  record:`pid`host`token`createdMs!(
    pid; first system "hostname"; .quant.instanceId; .z.p);
  .[set; (hsym `$owner; record); {::}];
  readback:@[get;hsym `$owner;{::}];
  ok:(readback~(::)) or (not .quant.validPid[string readback`pid]);
  if[ok; .quant.checkedRemoveLock[c]; :0b];
  1b}
.quant.writerRelease:{[p]
  ok:.quant.checkedRemoveLock[p];
  if[not ok; '`writer_release_failed];
  ::}

.quant.recoverOrphanLock:{[p]
  if[.quant.lockState[p]<>`orphan; :0b];
  :.quant.checkedRemoveLock p}

/ Immutable generation: components are written to a staging directory, synced,
/ then atomically renamed before the current pointer is published.
.quant.persist:{[path]
  p:.quant.pathString path;
  if[not .quant.safePath p; :`unsafe_path];
  c:.quant.canonicalPath p;
  if[not .quant.checkedSystem[("mkdir -p ",.quant.quotePath c)];
      :`mkdir_failed];
  if[not .quant.writerAcquire[c]; :`writer_busy];
  .quant.activePath::c;
  writeAll:{[c]
    gen:.quant.currentGeneration[c]+1;
    genText:string `long$gen;
    stagedPath:c,"/gen-",genText,".staging";
    finalPath:c,"/gen-",genText;
    if[.quant.checkedSystem[("test -d ",(.quant.quotePath finalPath))];
        :`generation_conflict];
    if[not .quant.checkedSystem[("mkdir ",.quant.quotePath stagedPath)];
        :`staging_failed];
    root:hsym `$stagedPath;
    .quant.generation::gen;
    savedQuotes:quotes; savedBars:bars;
    savedFeatures:.quant.featureHistory[]; savedSeen:.quant.seen;
    savedState:.quant.state[];
    savedMeta:(savedState;.quant.counts[]);
    savedValues:(savedQuotes;savedBars;savedFeatures;savedSeen;
      savedState;savedMeta);
    digests:();
    i:0;
    do[6;
      name:.quant.componentNames[i];
      path:.quant.filePath[root;name];
      path set savedValues[i];
      if[not .quant.syncFile[string path]; :`component_sync_failed];
      digests,:enlist (count savedValues[i];.quant.digest savedValues[i]);
      i:i+1];
    components:.quant.componentNames!digests;
    manifest:`schemaVersion`generation`components!(
      .quant.schemaVersion;gen;components);
    (.quant.filePath[root;`manifest]) set manifest;
    if[not .quant.syncFile[string .quant.filePath[root;`manifest]];
        :`manifest_sync_failed];
    if[not .quant.checkedSystem[
        ("mv -f ",(.quant.quotePath stagedPath)," ",
          (.quant.quotePath finalPath))];
        :`generation_publish_failed];
    if[not .quant.syncFile[finalPath]; :`generation_sync_failed];
    (.quant.filePath[hsym `$c;`currentTmp]) set gen;
    pointer:.quant.canonicalPath[c,"/currentTmp"];
    if[not .quant.syncFile[pointer]; :`pointer_sync_failed];
    if[not .quant.checkedSystem[
        ("mv -f ",(.quant.quotePath pointer)," ",
          (.quant.quotePath[c,"/current"]))];
        :`pointer_publish_failed];
    if[not .quant.syncFile[.quant.canonicalPath[c,"/current"]];
        :`parent_sync_failed];
    :`persisted};
  result:@[writeAll;c;{[err] .quant.writerRelease[.quant.activePath]; 'err}];
  .quant.writerRelease[.quant.activePath];
  :result}

/ Uncommitted staging generations are never promoted; remove them explicitly.

.quant.stagingNameValid:{[name]
  s:$[10h=type name; name; string name];
  if[all s like "gen-[0-9]*.staging"; :1b];
  :0b}
.quant.removeStaged:{[path;name]
  if[not .quant.stagingNameValid[name]; :0b];
  target:.quant.quotePath[(.quant.canonicalPath path),"/",string name];
  tool:.quant.quantBin[];
  if[not count tool; tool:"build/quant"];
  r:@[system;(tool," rmrf ",target);{`q_remove_error}];
  ok:((type r)=0h) and (1=count r) and (first r like "ok*");
  if[ok; .quant.sync[]];
  :ok}
.quant.recoverOrphans:{[p]
  path:.quant.pathString p;
  if[not .quant.safePath path; :`unsafe_path];
  if[0=count key hsym `$(.quant.canonicalPath path); :0];
  names:key hsym `$(.quant.canonicalPath path);
  staged:names where .quant.stagingNameValid each names;
  results:.quant.removeStaged[path] each staged;
  if[not all results; '`orphan_cleanup_failed];
  count staged}

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
