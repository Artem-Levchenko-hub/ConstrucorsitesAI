/* Managed by MAX Studio. This fallback is replaced by the saved business profile. */
export const omniaMaxConfig = {
  app_name: "MAX Mini App",
  app_type: "custom",
  summary: "Готовое мини-приложение для пользователей MAX",
  audience: "",
  primary_action: "Открыть приложение",
  features: [],
  style: "brand",
  brand_colors: "",
  content: [],
  operator: { legal_name: "", inn: "", ogrn: "", address: "" },
  support: {
    email: null,
    phone: "",
    response_time: "Ответим в течение 2 рабочих дней",
  },
  legal: {
    age_rating: "0+",
    has_sales: false,
    has_user_content: false,
    marketing_notifications: false,
    personal_data_consent: true,
    terms_accepted: false,
  },
  max_url_attached: false,
} as const;

export type OmniaMaxConfig = typeof omniaMaxConfig;
