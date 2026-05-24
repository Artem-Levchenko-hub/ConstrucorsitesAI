"use client";

import { motion } from "framer-motion";

type ShinyTextProps = {
  text: string;
  baseColor?: string;
  shineColor?: string;
  speed?: number;
  spread?: number;
  className?: string;
};

export function ShinyText({
  text,
  baseColor = "#A5C8FF",
  shineColor = "#ffffff",
  speed = 4.5,
  spread = 110,
  className = "",
}: ShinyTextProps) {
  const gradient = `linear-gradient(${spread}deg, ${baseColor} 0%, ${baseColor} 40%, ${shineColor} 50%, ${baseColor} 60%, ${baseColor} 100%)`;

  return (
    <motion.span
      className={className}
      style={{
        backgroundImage: gradient,
        backgroundSize: "200% 100%",
        backgroundClip: "text",
        WebkitBackgroundClip: "text",
        WebkitTextFillColor: "transparent",
        color: "transparent",
        display: "inline-block",
      }}
      animate={{ backgroundPosition: ["200% 0%", "-100% 0%"] }}
      transition={{ duration: speed, ease: "linear", repeat: Infinity }}
    >
      {text}
    </motion.span>
  );
}
