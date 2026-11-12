"use client";

import { useParams } from "next/navigation";
import { ArtifactPage } from "@/components/artifacts/artifact-view";

export default function Page() {
  const { key } = useParams<{ key: string }>();
  return <ArtifactPage key={key} artifactKey={key} />;
}
