/*
 Bright Mind Tutor V30.29
 Teacher portal workflow layer.
 Non-destructive and API-agnostic.
*/
(function () {
  "use strict";

  var state = {
    initialized:false,
    classRoom:null,
    lesson:null,
    assignment:null,
    assessment:null
  };

  function fn(names){
    for(var i=0;i<names.length;i++){
      if(typeof window[names[i]]==="function") return window[names[i]];
    }
    return null;
  }

  function emit(name,detail){
    document.dispatchEvent(new CustomEvent(name,{bubbles:true,detail:detail||{}}));
  }

  function openClass(classRoom){
    state.classRoom=classRoom||null;
    var f=fn(["openClass","viewClass","loadClass"]);
    if(f && classRoom) return f(classRoom);
    emit("bmt:teacher-class-open",{classRoom:classRoom});
  }

  function openLesson(lesson){
    state.lesson=lesson||null;
    var f=fn(["openTeacherLesson","editLesson","loadLesson"]);
    if(f && lesson) return f(lesson);
    emit("bmt:teacher-lesson-open",{lesson:lesson});
  }

  function saveAssignment(assignment){
    state.assignment=assignment||null;
    var f=fn(["saveAssignment","createAssignment","updateAssignment"]);
    if(f && assignment) return f(assignment);
    emit("bmt:teacher-assignment-save",{assignment:assignment});
  }

  function saveAssessment(assessment){
    state.assessment=assessment||null;
    var f=fn(["saveAssessment","createAssessment","updateAssessment"]);
    if(f && assessment) return f(assessment);
    emit("bmt:teacher-assessment-save",{assessment:assessment});
  }

  function gradeSubmission(data){
    var f=fn(["gradeSubmission","saveGrade","submitGrade"]);
    if(f && data) return f(data);
    emit("bmt:teacher-grade-submission",{data:data});
  }

  function init(root){
    root=root||document;
    if(state.initialized) return;
    state.initialized=true;

    root.querySelectorAll("[data-bmt-teacher-class]").forEach(function(el){
      el.addEventListener("click",function(){
        openClass({
          id:el.dataset.classId,
          title:el.dataset.classTitle||el.textContent.trim()
        });
      });
    });

    root.querySelectorAll("[data-bmt-teacher-lesson]").forEach(function(el){
      el.addEventListener("click",function(){
        openLesson({
          id:el.dataset.lessonId,
          title:el.dataset.lessonTitle||el.textContent.trim()
        });
      });
    });

    root.querySelectorAll("[data-bmt-teacher-save-assignment]").forEach(function(el){
      el.addEventListener("click",function(){
        saveAssignment({id:el.dataset.assignmentId});
      });
    });

    root.querySelectorAll("[data-bmt-teacher-save-assessment]").forEach(function(el){
      el.addEventListener("click",function(){
        saveAssessment({id:el.dataset.assessmentId});
      });
    });

    root.querySelectorAll("[data-bmt-teacher-grade]").forEach(function(el){
      el.addEventListener("click",function(){
        gradeSubmission({
          submissionId:el.dataset.submissionId,
          score:el.dataset.score
        });
      });
    });

    emit("bmt:teacher-ready");
  }

  if(document.readyState==="loading"){
    document.addEventListener("DOMContentLoaded",function(){init();});
  }else init();

  window.BMT_TEACHER_PORTAL={
    init:init,
    state:state,
    openClass:openClass,
    openLesson:openLesson,
    saveAssignment:saveAssignment,
    saveAssessment:saveAssessment,
    gradeSubmission:gradeSubmission
  };
})();
