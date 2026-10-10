"use strict";
const memberForm = document.getElementById("member-batch-form");
const memberSave = document.getElementById("save-batch-btn");
if (memberForm && memberSave) {
  const disableMemberSave = () => {
    memberSave.disabled = true;
    document.getElementById("save-disabled-msg").hidden = false;
  };
  memberForm.addEventListener("input", disableMemberSave);
  memberForm.addEventListener("change", disableMemberSave);
}
