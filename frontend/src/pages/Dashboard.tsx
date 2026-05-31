import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../api/client";

export default function Dashboard() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
  });

  return (
    <div className="page">
      <h1 className="page__title">Dashboard</h1>

      <div className="card">
        <h2 className="card__title">Backend status</h2>
        {isLoading && <p className="muted">Checking…</p>}
        {isError && <p className="status status--error">Cannot reach backend: {String(error)}</p>}
        {data && (
          <ul className="kv">
            <li>
              <span>Status</span>
              <span className={data.status === "ok" ? "status status--ok" : "status status--error"}>
                {data.status}
              </span>
            </li>
            <li>
              <span>Version</span>
              <span>{data.version}</span>
            </li>
            <li>
              <span>Database</span>
              <span className={data.database === "ok" ? "status status--ok" : "status status--error"}>
                {data.database}
              </span>
            </li>
            <li>
              <span>Privacy mode</span>
              <span>{data.privacy_mode}</span>
            </li>
          </ul>
        )}
      </div>

      <div className="card">
        <h2 className="card__title">Welcome</h2>
        <p className="muted">
          This is the Stage 0 skeleton. CSV import, categories, rules, splits,
          projects, budgets and the review queue arrive in later stages. The
          dashboard cards and charts (spec §25.1) will live here.
        </p>
      </div>
    </div>
  );
}
