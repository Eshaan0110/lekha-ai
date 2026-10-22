// The SYNTHETIC dev users (apps/scripts/seed_dev_data.py). Development sign-in only: the API
// refuses this header outside local/test, and real sign-in will be Group SSO.

export interface Persona {
  id: string;
  name: string;
  role: string;
  note: string;
}

export const PERSONAS: Persona[] = [
  { id: "asha", name: "Asha", role: "Employee, Alpha", note: "Reads Alpha's everyday policies" },
  { id: "meera", name: "Meera", role: "Legal, Alpha", note: "Also reads restricted legal and pay documents" },
  { id: "ravi", name: "Ravi", role: "Employee, Beta plant", note: "Reads only Beta plant documents" },
  { id: "chitra", name: "Chitra", role: "Curator, Alpha", note: "Uploads documents for Alpha" },
  { id: "bina", name: "Bina", role: "Curator, Beta plant", note: "Uploads documents for Beta" },
  { id: "tara", name: "Tara", role: "Auditor, Alpha", note: "Reads Alpha's audit log, no documents" },
  { id: "hari", name: "Hari", role: "Approver, Alpha", note: "Approves Alpha's tickets" },
  { id: "uma", name: "Uma", role: "Approver, Beta plant", note: "Approves Beta's tickets" },
  { id: "nobody", name: "Nobody", role: "No roles", note: "Has access to nothing" },
];
