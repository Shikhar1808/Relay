import { connection } from "next/server";

// The browser calls the backend directly, so it needs the backend's public
// URL. Served from here, read at request time, so one built image works in
// every environment (NEXT_PUBLIC_ variables are frozen at build time).
export async function GET() {
  await connection();
  return Response.json({ apiUrl: process.env.API_URL ?? "http://localhost:8000" });
}
