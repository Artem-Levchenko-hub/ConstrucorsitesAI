/* Managed by MAX Studio. This fallback is replaced by the saved business profile. */
export type OmniaMaxContentItem = {
  id: string;
  title: string;
  description: string;
  price: string;
  action_label: string;
  active: boolean;
};

export type OmniaMaxConfig = {
  app_name: string;
  app_type: "loyalty" | "catalog" | "booking" | "event" | "education" | "custom";
  summary: string;
  audience: string;
  primary_action: string;
  features: string[];
  style: "brand" | "clean" | "bright";
  brand_colors: string;
  content: OmniaMaxContentItem[];
  operator: { legal_name: string; inn: string; ogrn: string; address: string };
  support: { email: string | null; phone: string; response_time: string };
  legal: {
    age_rating: "0+" | "6+" | "12+" | "16+" | "18+";
    has_sales: boolean;
    has_user_content: boolean;
    marketing_notifications: boolean;
    personal_data_consent: boolean;
    terms_accepted: boolean;
  };
};

export const omniaMaxConfig: OmniaMaxConfig = {
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
};
