import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { I18nProvider } from "@/lib/i18n/context";

export function renderWithProviders(ui: ReactElement) {
  return render(<I18nProvider>{ui}</I18nProvider>);
}
