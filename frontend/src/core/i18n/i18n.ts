import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";

const STORAGE_KEY = "agentium_lang";

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    "zh-CN": { translation: zhCN },
  },
  lng: (typeof localStorage !== "undefined" && localStorage.getItem(STORAGE_KEY)) || "en",
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export function setAgentiumLanguage(lng: string): void {
  void i18n.changeLanguage(lng);
  try {
    localStorage.setItem(STORAGE_KEY, lng);
  } catch {
    /* ignore */
  }
}

export default i18n;
