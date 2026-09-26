/* Managed by Yleum. This fallback is replaced by the saved business profile. */
export type YleumMaxContentItem = {
  id: string;
  title: string;
  category: string;
  description: string;
  price: string;
  availability: "in_stock" | "on_request" | "out_of_stock";
  options: string[];
  image_url: string;
  action_label: string;
  active: boolean;
};

export type YleumMaxConfig = {
  app_name: string;
  app_type: "loyalty" | "catalog" | "booking" | "event" | "education" | "custom";
  summary: string;
  audience: string;
  primary_action: string;
  features: string[];
  style: "brand" | "clean" | "bright";
  brand_colors: string;
  content: YleumMaxContentItem[];
  operator: { legal_name: string };
  support: { email: string | null; response_time: string };
  legal: {
    age_rating: "0+" | "6+" | "12+" | "16+" | "18+";
    has_sales: boolean;
    has_user_content: boolean;
    marketing_notifications: boolean;
    personal_data_consent: boolean;
    terms_accepted: boolean;
    policy_url: string;
  };
};

export const omniaMaxConfig: YleumMaxConfig = {
  app_name: "MAX Mini App",
  app_type: "custom",
  summary: "Готовое мини-приложение для пользователей MAX",
  audience: "",
  primary_action: "Открыть приложение",
  features: [],
  style: "brand",
  brand_colors: "",
  content: [],
  operator: { legal_name: "" },
  support: {
    email: null,
    response_time: "Ответим в течение 2 рабочих дней",
  },
  legal: {
    age_rating: "0+",
    has_sales: false,
    has_user_content: false,
    marketing_notifications: false,
    personal_data_consent: true,
    terms_accepted: false,
    policy_url: "",
  },
};

/* Старые имена оставлены навсегда как синонимы: приложения, опубликованные до
   переименования, содержат вызовы с ними, и перегенерировать их мы не будем. */
export type OmniaMaxConfig = YleumMaxConfig;
export type OmniaMaxContentItem = YleumMaxContentItem;
