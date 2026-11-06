"use client";

import { ChevronDown, CirclePlay } from "lucide-react";
import { useEffect, useRef } from "react";
import { guideVideoId, type GuideLessonId } from "@/lib/guide-lessons";

export function GuideVideoLesson({ lesson, title }: { lesson: GuideLessonId; title: string }) {
  const disclosure = useRef<HTMLDetailsElement>(null);
  const player = useRef<HTMLVideoElement>(null);
  const id = guideVideoId(lesson);
  const mediaPath = `/guide/lessons/2026-11-06/${lesson}`;

  useEffect(() => {
    const openLinkedLesson = () => {
      if (disclosure.current && window.location.hash === `#${id}`) disclosure.current.open = true;
    };
    openLinkedLesson();
    window.addEventListener("hashchange", openLinkedLesson);
    return () => window.removeEventListener("hashchange", openLinkedLesson);
  }, [id]);

  return (
    <details id={id} ref={disclosure} className="guide-video-lesson" onToggle={event => {
      if (!event.currentTarget.open) player.current?.pause();
    }}>
      <summary aria-label={`Смотреть видео «${title}»`}>
        <CirclePlay size={20} aria-hidden="true" />
        <span>Смотреть видео</span>
        <ChevronDown size={16} aria-hidden="true" className="guide-video-chevron" />
      </summary>
      <video ref={player} aria-label={title} controls playsInline preload="none"
        width={1280} height={720} poster={`${mediaPath}.jpg`}>
        <source src={`${mediaPath}.mp4`} type="video/mp4" />
        <track src={`${mediaPath}.vtt`} kind="captions" srcLang="ru" label="Русский" default />
        <a href={`${mediaPath}.mp4`}>Открыть видеофайл</a>
      </video>
    </details>
  );
}
