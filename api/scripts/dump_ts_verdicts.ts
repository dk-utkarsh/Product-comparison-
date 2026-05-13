import { triage } from "../../lib/match-triage";

const CASES: Array<[string, string]> = [
  ["3M Filtek Z350 XT Shade A2", "3M Filtek Z350 XT A2"],
  ["3M Filtek Z350 XT Shade A2", "3M Filtek Z350 XT Shade A3"],
  ["3M Filtek Z350 XT", "GC Fuji IX GP Capsules"],
  ["Dentsply ProTaper F2", "Dentsply ProTaper F1"],
  ["Dentsply ProTaper F2 25", "Dentsply ProTaper F2"],
  ["GC Fuji IX GP Capsules", "GC Fuji IX GP Powder"],
  ["GC Fuji IX GP Capsules", "GC Fuji IX GP Capsules - Pack Of 50"],
  ["Endo File #25", "Endo File #15"],
  ["Cotton Rolls Pack Of 500", "Cotton Rolls"],
  ["Chlorhexidine 2% Mouthwash", "Chlorhexidine 5% Mouthwash"],
  ["MBT Bracket .022 Slot", "Roth Bracket .022 Slot"],
  ["MBT Bracket .022 Slot", "MBT Bracket .018 Slot"],
  ["3M Espe Adper Single Bond 2", "3M Espe Adper Single Bond Universal"],
  ["Septodont Septanest 1:100000", "Septodont Septanest 1:200000"],
  ["Woodpecker UDS-J Scaler", "Woodpecker UDS-N3 Scaler"],
  ["Composite Resin - Buy Online", "Composite Resin"],
  ["3M Filtek Refill", "3M Filtek Kit"],
  ["Monitor LCD 24 inch", "Dental Crown"],
  ["GC Fuji IX GP Extra", "GC FujiIX GP Capsules"],
  ["Putty Light Body", "Putty Heavy Body"],
];

const results = CASES.map(([s, c]) => {
  const t = triage(s, c);
  return { search: s, candidate: c, verdict: t.verdict, similarity: t.similarity };
});

console.log(JSON.stringify(results, null, 2));
