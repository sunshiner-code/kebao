"use client";

import { useParams } from "next/navigation";
import { RunConsole } from "@/components/run-console";

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  return <RunConsole key={id} runId={Number(id)} />;
}
