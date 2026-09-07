/**
 * Example rows, shown until the API is wired up.
 *
 * These are illustrative, not real tenders, and the interface says so. They
 * exist because an empty dashboard shows nothing about what the screen does,
 * and because the layout has to be designed against realistic values: long
 * authority names, eight-digit reference numbers, and amounts spanning lakhs
 * to hundreds of crores.
 */

export type IngestStatus = "complete" | "extracting" | "ocr" | "failed";
export type DecisionStatus = "bid" | "no_bid" | "review" | "pending";

export interface TenderRow {
  id: string;
  reference: string;
  title: string;
  authority: string;
  value: number | null;
  closingDate: string;
  workCategory: string;
  ingest: IngestStatus;
  decision: DecisionStatus;
  /** Set once a decision exists. Null while pending. */
  score: number | null;
  /** The gate that settled a no-bid, shown instead of a bare score. */
  decidingGate: string | null;
  corrigendumCount: number;
}

const daysFromNow = (days: number): string => {
  const date = new Date();
  date.setDate(date.getDate() + days);
  date.setHours(15, 0, 0, 0);
  return date.toISOString();
};

export const SAMPLE_TENDERS: TenderRow[] = [
  {
    id: "1",
    reference: "NHAI/2026/PKG-14/EPC",
    title: "Four-laning of Kanchipuram–Vellore section, Package 14",
    authority: "National Highways Authority of India",
    value: 2_845_000_000,
    closingDate: daysFromNow(2),
    workCategory: "Roads & Highways",
    ingest: "complete",
    decision: "review",
    score: 61,
    decidingGate: null,
    corrigendumCount: 2,
  },
  {
    id: "2",
    reference: "CPWD/CH/2026/0442",
    title: "Construction of administrative block, IIT Madras campus",
    authority: "Central Public Works Department",
    value: 486_000_000,
    closingDate: daysFromNow(5),
    workCategory: "Buildings",
    ingest: "complete",
    decision: "bid",
    score: 82,
    decidingGate: null,
    corrigendumCount: 0,
  },
  {
    id: "3",
    reference: "TNPWD/WRO/2026/117",
    title: "Rehabilitation of Palar river flood embankment, Reach 3",
    authority: "Tamil Nadu Public Works Department",
    value: 92_500_000,
    closingDate: daysFromNow(11),
    workCategory: "Water Resources",
    ingest: "extracting",
    decision: "pending",
    score: null,
    decidingGate: null,
    corrigendumCount: 1,
  },
  {
    id: "4",
    reference: "MES/CE-CHN/2026/88",
    title: "Married accommodation project, Phase II, Avadi",
    authority: "Military Engineer Services",
    value: 1_240_000_000,
    closingDate: daysFromNow(18),
    workCategory: "Buildings",
    ingest: "complete",
    decision: "no_bid",
    score: 24,
    decidingGate: "Average annual turnover",
    corrigendumCount: 0,
  },
  {
    id: "5",
    reference: "CMRL/UG-02/2026",
    title: "Underground station box and associated tunnels, Corridor 5",
    authority: "Chennai Metro Rail Limited",
    value: 8_900_000_000,
    closingDate: daysFromNow(24),
    workCategory: "Urban Transport",
    ingest: "ocr",
    decision: "pending",
    score: null,
    decidingGate: null,
    corrigendumCount: 3,
  },
  {
    id: "6",
    reference: "TANGEDCO/CIV/2026/019",
    title: "Civil works for 400kV substation, Hosur",
    authority: "Tamil Nadu Generation and Distribution Corporation",
    value: 7_850_000,
    closingDate: daysFromNow(-3),
    workCategory: "Power",
    ingest: "failed",
    decision: "pending",
    score: null,
    decidingGate: null,
    corrigendumCount: 0,
  },
];
