package com.example.shop.checkout

import android.os.Bundle
import android.view.View
import androidx.constraintlayout.widget.ConstraintLayout
import androidx.constraintlayout.widget.ConstraintSet
import androidx.fragment.app.Fragment
import com.example.shop.R

class CheckoutFragment : Fragment(R.layout.fragment_checkout) {

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        if (resources.configuration.screenHeightDp < COMPACT_HEIGHT_DP) {
            applyCompactMode(view as ConstraintLayout)
        }
    }

    private fun applyCompactMode(root: ConstraintLayout) {
        val set = ConstraintSet()
        set.clone(root)
        set.setVisibility(R.id.checkout_subtitle, View.GONE)
        set.connect(R.id.subtotal_row, ConstraintSet.TOP, R.id.checkout_title, ConstraintSet.BOTTOM)
        set.applyTo(root)
    }

    private companion object {
        const val COMPACT_HEIGHT_DP = 600
    }
}
