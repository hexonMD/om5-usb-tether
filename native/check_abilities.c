/* Inspect the installed driver registry without opening a USB device. */
#include <stdio.h>
#include <gphoto2/gphoto2-abilities-list.h>
#include <gphoto2/gphoto2-result.h>

int main(void)
{
    CameraAbilitiesList *list = NULL;
    CameraAbilities model;
    int index, result = 1;
    if (gp_abilities_list_new(&list) < GP_OK)
        return 1;
    if (gp_abilities_list_load(list, NULL) < GP_OK)
        goto done;
    index = gp_abilities_list_lookup_model(list, "OM System OM-5 (USB Remote)");
    if (index < GP_OK || gp_abilities_list_get_abilities(list, index, &model) < GP_OK)
        goto done;
    if (model.usb_vendor != 0x33a2 || model.usb_product != 0x0138 ||
        !(model.operations & GP_OPERATION_CAPTURE_IMAGE) || !(model.operations & GP_OPERATION_CONFIG))
        goto done;
    printf("%s  %04x:%04x  capture + configuration\n", model.model, model.usb_vendor, model.usb_product);
    result = 0;
done:
    gp_abilities_list_free(list);
    return result;
}
