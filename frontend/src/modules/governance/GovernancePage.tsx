import * as React from "react";
import { useTranslation } from "react-i18next";
import { EffectivePolicyPanel } from "./EffectivePolicyPanel";

export function GovernancePage(): React.ReactElement {
  const { t } = useTranslation();
  return (
    <div className="space-y-3 text-sm text-slate-700">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.governance")}</h1>
      <EffectivePolicyPanel />
      <p className="mt-3">{t("governancePage.intro")}</p>
      <ul className="list-inside list-disc space-y-2">
        <li>
          <span className="font-medium">{t("governancePage.docFixedIncome")}</span>:{" "}
          <code className="rounded bg-slate-100 px-1 text-xs">docs/architecture/domain-governance-fixed-income.md</code>
        </li>
        <li>
          <span className="font-medium">{t("governancePage.docLoader")}</span>:{" "}
          <code className="rounded bg-slate-100 px-1 text-xs">docs/codemap/governance/domain_pack_loader.md</code> ·
          source{" "}
          <code className="rounded bg-slate-100 px-1 text-xs">src/agentium/governance/domain_pack_loader.py</code>
        </li>
      </ul>
      <p>
        {t("governancePage.manifestFile")} · {t("governancePage.envHint")}
      </p>
      <p className="text-xs text-slate-500">
        Operators configure pack roots and signing secrets in the runtime container (see{" "}
        <code className="rounded bg-slate-100 px-1">load_settings</code> / deployment docs)—not via this UI.
      </p>
    </div>
  );
}
