"use client";
import { motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";

export default function MotionCard({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  const reduce = useReducedMotion();
  return (
    <motion.section
      className={`card ${className}`}
      whileHover={reduce ? undefined : { y: -2 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
    >
      {children}
    </motion.section>
  );
}
