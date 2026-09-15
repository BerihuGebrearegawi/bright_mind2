/*
 Bright Mind Tutor V30.28
 Student portal workflow layer.
 Non-destructive and API-agnostic: integrates with existing functions when present.
*/
(function () {
  "use strict";

  var state = {
    initialized: false,
    course: null,
    lesson: null,
    assignment: null,
    assessment: null
  };

  function firstFunction(names) {
    for (var i=0;i<names.length;i++) {
      if (typeof window[names[i]] === "function") return window[names[i]];
    }
    return null;
  }

  function notify(type, message) {
    var event = new CustomEvent("bmt:student-notification", {
      bubbles: true,
      detail: { type: type, message: message }
    });
    document.dispatchEvent(event);
  }

  function renderText(selector, value) {
    var el=document.querySelector(selector);
    if(el) el.textContent = value == null ? "" : String(value);
  }

  function renderProgress(value) {
    var n=Number(value);
    if(!Number.isFinite(n)) return;
    n=Math.max(0,Math.min(100,n));
    document.querySelectorAll("[data-bmt-student-progress]").forEach(function(el){
      el.style.width=n+"%";
      el.setAttribute("aria-valuenow",String(n));
    });
    renderText("[data-bmt-student-progress-value]", Math.round(n)+"%");
  }

  function openCourse(course) {
    state.course=course || null;
    var fn=firstFunction(["openCourse","viewCourse","loadCourse"]);
    if(fn && course) return fn(course);
    document.dispatchEvent(new CustomEvent("bmt:student-course-open",{detail:{course:course}}));
  }

  function openLesson(lesson) {
    state.lesson=lesson || null;
    var fn=firstFunction(["openLesson","viewLesson","loadLesson"]);
    if(fn && lesson) return fn(lesson);
    document.dispatchEvent(new CustomEvent("bmt:student-lesson-open",{detail:{lesson:lesson}}));
  }

  function submitAssignment(assignment) {
    state.assignment=assignment || null;
    var fn=firstFunction(["submitAssignment","saveAssignment","sendAssignment"]);
    if(fn && assignment) return fn(assignment);
    document.dispatchEvent(new CustomEvent("bmt:student-assignment-submit",{detail:{assignment:assignment}}));
  }

  function submitAssessment(assessment) {
    state.assessment=assessment || null;
    var fn=firstFunction(["submitAssessment","finishAssessment","saveAssessment"]);
    if(fn && assessment) return fn(assessment);
    document.dispatchEvent(new CustomEvent("bmt:student-assessment-submit",{detail:{assessment:assessment}}));
  }

  function init(root) {
    root=root||document;
    if(state.initialized) return;
    state.initialized=true;

    root.querySelectorAll("[data-bmt-student-course]").forEach(function(el){
      el.addEventListener("click",function(){
        openCourse({
          id:el.dataset.courseId || el.getAttribute("data-course-id"),
          title:el.dataset.courseTitle || el.textContent.trim()
        });
      });
    });

    root.querySelectorAll("[data-bmt-student-lesson]").forEach(function(el){
      el.addEventListener("click",function(){
        openLesson({
          id:el.dataset.lessonId || el.getAttribute("data-lesson-id"),
          title:el.dataset.lessonTitle || el.textContent.trim()
        });
      });
    });

    root.querySelectorAll("[data-bmt-student-assignment-submit]").forEach(function(el){
      el.addEventListener("click",function(){
        submitAssignment({
          id:el.dataset.assignmentId || el.getAttribute("data-assignment-id")
        });
      });
    });

    root.querySelectorAll("[data-bmt-student-assessment-submit]").forEach(function(el){
      el.addEventListener("click",function(){
        submitAssessment({
          id:el.dataset.assessmentId || el.getAttribute("data-assessment-id")
        });
      });
    });

    root.querySelectorAll("[data-bmt-student-progress]").forEach(function(el){
      if(!el.hasAttribute("role")) el.setAttribute("role","progressbar");
      if(!el.hasAttribute("aria-valuemin")) el.setAttribute("aria-valuemin","0");
      if(!el.hasAttribute("aria-valuemax")) el.setAttribute("aria-valuemax","100");
    });

    notify("ready","Student portal workflow layer initialized.");
  }

  if(document.readyState==="loading"){
    document.addEventListener("DOMContentLoaded",function(){init();});
  }else init();

  window.BMT_STUDENT_PORTAL={
    init:init,
    state:state,
    openCourse:openCourse,
    openLesson:openLesson,
    submitAssignment:submitAssignment,
    submitAssessment:submitAssessment,
    setProgress:renderProgress
  };
})();
