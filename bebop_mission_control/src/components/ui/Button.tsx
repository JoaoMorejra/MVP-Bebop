import React from 'react';
import { cn } from '../../lib/format';

type Variant = 'primary' | 'quiet' | 'danger' | 'ghost';

const VARIANT: Record<Variant, string> = {
  primary:
    'bg-mint text-abyss border-mint font-semibold hover:bg-mint-bright active:bg-mint-deep',
  quiet:
    'bg-hull-deck text-frost border-strut hover:border-strut-bright hover:bg-hull-raise',
  danger:
    'bg-ember text-abyss border-ember font-semibold hover:brightness-110 active:bg-ember-deep',
  ghost:
    'bg-transparent text-haze border-transparent hover:text-frost hover:bg-hull-deck',
};

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  icon?: React.ReactNode;
}

export const Button: React.FC<ButtonProps> = ({
  variant = 'quiet',
  icon,
  className,
  children,
  ...rest
}) => (
  <button
    type="button"
    {...rest}
    className={cn(
      'inline-flex items-center justify-center gap-2 rounded-bezel border px-3.5 py-1.5 text-sm',
      'transition-all duration-150 ease-instrument',
      'disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:brightness-100',
      VARIANT[variant],
      className
    )}
  >
    {icon}
    {children}
  </button>
);
